"""CAN Monitor roundtrip and serial baseline benchmarks, independent of Tk."""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import secrets
import statistics
import struct
import threading
import time
from zoneinfo import ZoneInfo

from bridge_protocol import (ACK, CAN_RX, CAN_TEST, CAN_TX, ECHO, ECHO_REPLY,
                             Client, Decoder, Packet, WIRES, encode)


@dataclass(frozen=True)
class Config:
    target: str = 'can'
    rounds: int = 1000
    interval_ms: float = 10
    timeout_ms: float = 200
    window: int = 1
    warmup: int = 10
    request_id: int = 0x6e0
    reply_id: int = 0x6e1

    def validate(self):
        if self.target not in ('can', 'serial'):
            raise ValueError('Unknown test target')
        if not 1 <= self.rounds <= 100000 or not 0 <= self.warmup <= 1000:
            raise ValueError('样本数范围1～100000，预热数0～1000')
        if not math.isfinite(self.interval_ms) or not 0 <= self.interval_ms <= 60000:
            raise ValueError('发送间隔范围0～60000毫秒')
        if not math.isfinite(self.timeout_ms) or not 1 <= self.timeout_ms <= 10000:
            raise ValueError('超时范围1～10000毫秒')
        if not 1 <= self.window <= 256:
            raise ValueError('并发窗口范围1～256')
        if not 0 <= self.request_id <= 0x7ff or not 0 <= self.reply_id <= 0x7ff or self.request_id == self.reply_id:
            raise ValueError('请求与回复ID必须为不同的标准CAN ID (0..7FF)')


def percentile(values, percent):
    return sorted(values)[max(0, math.ceil(len(values) * percent / 100) - 1)] if values else None


def summarize(records, duration_s, duplicates=0, late=0, unrelated=0):
    rows = [r for r in records if r['phase'] == 'measure']
    samples = [r['rtt_ms'] for r in rows if r['status'] == 'ok']
    total = len(rows)
    received = len(samples)
    timeouts = sum(r['status'] == 'timeout' for r in rows)
    rejected = sum(r['status'] == 'rejected' for r in rows)
    cancelled = sum(r['status'] == 'cancelled' for r in rows)
    first = rows[0]['sent_ns'] if rows else None
    # Time to last valid reply, or timeout/cancellation if that ends later.
    elapsed = max(duration_s, 1e-9)
    jitters = [abs(b - a) for a, b in zip(samples, samples[1:])]
    ordered = sorted(samples)
    def quantile(percent):
        return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)] if ordered else None
    return dict(sent=total, received=received, timeouts=timeouts, rejected=rejected, cancelled=cancelled,
                missing=total - received, loss_percent=(total - received) * 100 / total if total else 0,
                min_ms=min(samples) if samples else None, mean_ms=statistics.fmean(samples) if samples else None,
                p50_ms=quantile(50), p95_ms=quantile(95), p99_ms=quantile(99),
                max_ms=max(samples) if samples else None, stddev_ms=statistics.pstdev(samples) if samples else None,
                jitter_ms=statistics.fmean(jitters) if jitters else None, duration_s=duration_s,
                tx_fps=total / elapsed, rx_fps=received / elapsed, payload_Bps=received * 8 / elapsed,
                duplicates=duplicates, late=late, unrelated=unrelated,
                samples_ms=samples[-200:], first_sent_ns=first)


def _stats(client):
    client.port.write(b'stats\r\n')
    lines = []
    for _ in range(4):
        lines.append(client.wait(lambda p: isinstance(p, str) and (p.startswith('mode=') or p.startswith('port=')), 2))
    return lines


def run(port, config: Config, cancel=None, progress=lambda snapshot: None):
    """Owns the serial reader for the run. Timestamps use monotonic_ns, before
    write and immediately after complete reply bytes arrive, before UI queues.
    """
    config.validate()
    cancel = cancel or threading.Event()
    original_timeout = port.timeout
    original = None
    records = []
    result = dict(config=asdict(config), started_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                  clock='time.monotonic_ns', path='host -> SHARK -> CAN Monitor -> SHARK -> host'
                  if config.target == 'can' else 'host -> SHARK ECHO -> host',
                  payload_bytes=8, connection=dict(port=getattr(port, 'port', None),
                      baudrate=getattr(port, 'baudrate', None)),
                  error=None, restore_errors=[], before_stats=[], after_stats=[])
    token = secrets.token_bytes(4)
    counts = dict(duplicates=0, late=0, unrelated=0)
    measured_start = measured_end = None
    try:
        port.timeout = .01
        port.reset_input_buffer()
        client = Client(port)
        original = client.info()
        result['device'] = original
        if original['mode'] == 'usb-ttl':
            raise ValueError('请先用按键切回CAN模式，再通过USB/USART1测试；USART2为专用RAW入口')
        client.command('wire binary')
        if config.target == 'can':
            client.request(CAN_TEST, b'\x00')  # Always external normal mode; never count internal loopback.
        result['before_stats'] = _stats(client)
        decoder = Decoder()
        sequence = 0
        pending, known, wire_sequences = {}, {}, {}
        last_publish = 0
        timeout_ns = int(config.timeout_ms * 1e6)
        interval_ns = int(config.interval_ms * 1e6)

        for phase, limit in (('warmup', config.warmup), ('measure', config.rounds)):
            if cancel.is_set():
                break
            sent = finished = 0
            phase_start = time.monotonic_ns()
            due = phase_start
            if phase == 'measure':
                measured_start = phase_start
            while finished < limit and not cancel.is_set():
                now = time.monotonic_ns()
                window = 1 if phase == 'warmup' else config.window
                if sent < limit and len(pending) < window and now >= due:
                    sequence += 1
                    data = token + struct.pack('<I', sequence)
                    packet = Packet(CAN_TX if config.target == 'can' else ECHO, data,
                                    config.request_id, sequence & 0xffff)
                    raw = encode(packet)
                    started = time.monotonic_ns()
                    if port.write(raw) != len(raw):
                        raise OSError('Incomplete performance-test serial write')
                    record = dict(sequence=sequence, phase=phase, payload_hex=data.hex(), sent_ns=started,
                                  received_ns=None, rtt_ms=None, status='pending', reject_code=None)
                    records.append(record)
                    pending[sequence] = record
                    known[sequence] = record
                    wire_sequences[sequence & 0xffff] = sequence
                    sent += 1
                    # No catch-up bursts after OS scheduling delays.
                    due = max(due + interval_ns, started + interval_ns)
                now = time.monotonic_ns()
                for seq, record in list(pending.items()):
                    if now - record['sent_ns'] >= timeout_ns:
                        record['status'] = 'timeout'
                        record['received_ns'] = now
                        del pending[seq]
                        wire_sequences.pop(seq & 0xffff, None)
                        finished += 1
                if finished >= limit:
                    break
                next_send = due if sent < limit and len(pending) < window else now + 10_000_000
                expires = min((r['sent_ns'] + timeout_ns for r in pending.values()), default=now + 10_000_000)
                port.timeout = max(0, min(.01, (min(next_send, expires) - now) / 1e9))
                data = port.read(min(port.in_waiting, 65536) or 1)
                arrived = time.monotonic_ns()
                for packet in decoder.feed(data):
                    if not isinstance(packet, Packet):
                        continue
                    if packet.type == ACK and len(packet.data) == 2 and packet.data[0] == CAN_TX and packet.data[1]:
                        seq = wire_sequences.get(packet.sequence)
                        if seq in pending:
                            record = pending.pop(seq)
                            wire_sequences.pop(packet.sequence, None)
                            record.update(status='rejected', received_ns=arrived, reject_code=packet.data[1])
                            finished += 1
                        continue
                    expected_type = CAN_RX if config.target == 'can' else ECHO_REPLY
                    if (packet.type != expected_type or (config.target == 'can' and
                            (packet.id != config.reply_id or packet.flags & 1))):
                        continue
                    if len(packet.data) != 8 or packet.data[:4] != token:
                        counts['unrelated'] += 1
                        continue
                    seq = struct.unpack_from('<I', packet.data, 4)[0]
                    record = known.get(seq)
                    if record is None:
                        counts['unrelated'] += 1
                    elif seq not in pending:
                        counts['duplicates' if record['status'] == 'ok' else 'late'] += 1
                    elif arrived - record['sent_ns'] > timeout_ns:
                        record.update(status='timeout', received_ns=arrived)
                        pending.pop(seq)
                        wire_sequences.pop(seq & 0xffff, None)
                        counts['late'] += 1
                        finished += 1
                    else:
                        record.update(status='ok', received_ns=arrived, rtt_ms=(arrived - record['sent_ns']) / 1e6)
                        pending.pop(seq)
                        wire_sequences.pop(seq & 0xffff, None)
                        finished += 1
                if arrived - last_publish >= 100_000_000:
                    elapsed = (arrived - measured_start) / 1e9 if measured_start else 0
                    progress(dict(phase=phase, summary=summarize(records, elapsed, **counts)))
                    last_publish = arrived
            if cancel.is_set():
                for record in pending.values():
                    record.update(status='cancelled', received_ns=time.monotonic_ns())
                pending.clear()
            if phase == 'warmup' and limit and not cancel.is_set():
                ok = sum(r['status'] == 'ok' for r in records if r['phase'] == 'warmup')
                if not ok:
                    hint = (f'请开启 CAN Monitor 的{config.request_id:X}→{config.reply_id:X}回传并检查接线'
                            if config.target == 'can' else '请检查串口连接和协议模式')
                    raise TimeoutError('预热没有收到回传：' + hint)
            if phase == 'measure':
                measured_end = time.monotonic_ns()
        result['parse_errors'] = decoder.errors
        port.timeout = .01
        result['after_stats'] = _stats(Client(port))
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            cancel.set()
        else:
            result['error'] = str(exc)
        now = time.monotonic_ns()
        if measured_start is not None and measured_end is None:
            measured_end = now
        for record in records:
            if record['status'] == 'pending':
                record.update(status='cancelled', received_ns=now)
    finally:
        if original:
            port.timeout = .01
            client = Client(port)
            if config.target == 'can':
                try:
                    client.request(CAN_TEST, bytes([original['can_state']]))
                except Exception as exc:
                    result['restore_errors'].append(str(exc))
            try:
                client.command('wire ' + WIRES[original['wires'][original['port']]])
            except Exception as exc:
                result['restore_errors'].append(str(exc))
        port.timeout = original_timeout
    if measured_start is not None:
        duration = ((measured_end or time.monotonic_ns()) - measured_start) / 1e9
    else:
        duration = 0
    result.update(cancelled=cancel.is_set(), records=records, summary=summarize(records, duration, **counts))
    progress(dict(phase='done', summary=result['summary']))
    return result


def export(result, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = path.with_suffix('.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    with path.with_suffix('.csv').open('w', newline='') as output:
        fields = ('sequence', 'phase', 'status', 'payload_hex', 'sent_ns', 'received_ns', 'rtt_ms', 'reject_code')
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result['records'])
    return path
