"""Protocol-free serial roundtrip test for USART2 -> CAN -> Monitor -> USART2."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

MAX_BYTES = 1024 * 1024


def ascii_preview(data):
    return ''.join(chr(b) if b in (9, 10, 13) or 32 <= b < 127 else f'\\x{b:02X}' for b in data)


def long_text(length):
    if not 1 <= length <= MAX_BYTES:
        raise ValueError('文本长度范围1～1048576字节')
    rows, size, index = [], 0, 0
    while size < length:
        row = f'SHARK RAW {index:06d} | ABCDEFGHIJKLMNOPQRSTUVWXYZ | 0123456789 | uart2-can-uart2\n'
        rows.append(row)
        size += len(row)
        index += 1
    return ''.join(rows)[:length]


@dataclass(frozen=True)
class Config:
    payload: bytes
    chunk_size: int = 32
    interval_ms: float = 5
    timeout_ms: float = 2000
    window_bytes: int = 256

    def validate(self):
        if not 1 <= len(self.payload) <= MAX_BYTES:
            raise ValueError('RAW文本长度范围1～1048576字节')
        if not 1 <= self.chunk_size <= 256 or not self.chunk_size <= self.window_bytes <= 4096:
            raise ValueError('每次写入1～256字节，在途窗口不得小于写入长度且最多4096字节')
        if not math.isfinite(self.interval_ms) or not 0 <= self.interval_ms <= 60000:
            raise ValueError('间隔范围0～60000毫秒')
        if not math.isfinite(self.timeout_ms) or not 10 <= self.timeout_ms <= 60000:
            raise ValueError('超时范围10～60000毫秒')


def run(port, config, cancel=None, progress=lambda snapshot: None):
    """Send only user bytes; no INFO, commands, framing, CRC or extra newline.
    Bound unacknowledged bytes, compare continuously and stop on a stalled echo.
    A 100ms quiet guard also detects trailing duplicate data after a full reply.
    """
    config.validate()
    cancel = cancel or threading.Event()
    original_timeout = port.timeout
    received = bytearray()
    sent = 0
    error = mismatch = None
    started_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    started = last_rx = last_progress = due = time.monotonic()
    completed_at = None
    def snapshot():
        return dict(sent=sent, received=len(received), total=len(config.payload),
                    elapsed_s=time.monotonic() - started, error=error,
                    preview=ascii_preview(received[-16384:]))
    try:
        port.reset_input_buffer()
        while not cancel.is_set():
            now = time.monotonic()
            outstanding = sent - len(received)
            allowance = config.window_bytes - outstanding
            if sent < len(config.payload) and allowance > 0 and now >= due:
                chunk = config.payload[sent:sent + min(config.chunk_size, allowance)]
                if port.write(chunk) != len(chunk):
                    raise OSError('RAW串口写入不完整')
                if not outstanding:
                    last_rx = time.monotonic()
                sent += len(chunk)
                due = time.monotonic() + config.interval_ms / 1000
            now = time.monotonic()
            can_send = sent < len(config.payload) and sent - len(received) < config.window_bytes
            port.timeout = max(0, min(.01, due - now)) if can_send else .01
            data = port.read(min(port.in_waiting, 65536) or 1)
            now = time.monotonic()
            if data:
                last_rx = now
                offset = len(received)
                received.extend(data)
                expected = config.payload[offset:min(sent, offset + len(data))]
                if data != expected:
                    mismatch = offset + next((i for i, (a, b) in enumerate(zip(data, expected)) if a != b), len(expected))
                    error = f'返回数据不一致：首个差异在字节偏移 {mismatch}（从0开始）'
                    break
            if sent == len(config.payload) and len(received) == sent:
                completed_at = completed_at or now
                if now - completed_at >= .1:
                    break
            elif sent > len(received) and now - last_rx >= config.timeout_ms / 1000:
                error = '等待RAW回传超时；检查USART2接线、CAN ID与Monitor的RAW原样回传开关'
                break
            if now - last_progress >= .1:
                progress(snapshot())
                last_progress = now
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            cancel.set()
        else:
            error = str(exc)
    finally:
        port.timeout = original_timeout
    result = snapshot()
    result.update(passed=not error and not cancel.is_set() and bytes(received) == config.payload,
                  cancelled=cancel.is_set(), mismatch_offset=mismatch,
                  started_at=started_at,
                  port=getattr(port, 'port', None), baudrate=getattr(port, 'baudrate', None),
                  chunk_size=config.chunk_size, interval_ms=config.interval_ms,
                  timeout_ms=config.timeout_ms, window_bytes=config.window_bytes,
                  payload_hex=config.payload.hex(), received_hex=received.hex(),
                  sha256_sent=hashlib.sha256(config.payload[:sent]).hexdigest(),
                  sha256_received=hashlib.sha256(received).hexdigest(),
                  path='host -> UART2 -> SHARK -> CAN -> Monitor same-ID echo -> CAN -> SHARK -> UART2 -> host')
    progress(result)
    return result


def export(result, filename):
    path = Path(filename).with_suffix('.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.with_suffix('.tx.txt').write_bytes(bytes.fromhex(result['payload_hex']))
    path.with_suffix('.rx.txt').write_bytes(bytes.fromhex(result['received_hex']))
    return path
