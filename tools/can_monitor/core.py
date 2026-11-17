"""Capture and statistics, independent of the GUI and the device SDK."""
from __future__ import annotations

import csv
import json
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

MOTORS = {1: "左后", 2: "右后", 3: "右前", 4: "左前", 6: "云台 yaw"}
DLC_LENGTHS = tuple(range(9)) + (12, 16, 20, 24, 32, 48, 64)


@dataclass(frozen=True)
class Frame:
    host_ns: int
    wall_ns: int
    can_id: int
    data: bytes
    channel: int = 0
    extended: bool = False
    remote: bool = False
    fd: bool = False
    brs: bool = False
    direction: str = "RX"
    device_timestamp: int | None = None
    dlc: int = 8
    esi: bool = False
    ack: bool = False

    @property
    def group(self) -> str:
        prefix = f"CH{self.channel} {self.direction} {'E' if self.extended else 'S'}"
        if not (self.extended or self.remote or self.fd) and len(self.data) == 8:
            if self.can_id == 0x10 and (self.data[0] & 15) in MOTORS:
                motor = self.data[0] & 15
                return f"{prefix} 010 / 反馈{motor:02X} {MOTORS[motor]}"
            if self.can_id in MOTORS:
                return f"{prefix} {self.can_id:03X} / 命令 {MOTORS[self.can_id]}"
        return f"{prefix} {self.can_id:03X}"


@lru_cache(maxsize=8192)
def classic_frame_bits(can_id: int, data: bytes, extended: bool = False,
                       remote: bool = False, dlc: int = 8) -> int:
    """Successful classic CAN frame, CRC15 + actual stuffing + 3-bit IFS.

    Does not include error/overload frames, failed attempts or idle time.
    """
    bits = [0]  # SOF

    def put(value: int, width: int) -> None:
        bits.extend((value >> k) & 1 for k in range(width - 1, -1, -1))

    if extended:
        put(can_id >> 18, 11)
        bits.extend((1, 1))  # SRR, IDE
        put(can_id & 0x3FFFF, 18)
        bits.extend((int(remote), 0, 0))  # RTR, r1, r0
    else:
        put(can_id, 11)
        bits.extend((int(remote), 0, 0))  # RTR, IDE, r0
    put(dlc, 4)
    if not remote:
        for byte in data:
            put(byte, 8)
    crc = 0
    for bit in bits:
        carry = ((crc >> 14) & 1) ^ bit
        crc = (crc << 1) & 0x7FFF
        if carry:
            crc ^= 0x4599
    put(crc, 15)
    last, run, stuffed = -1, 0, 0
    for bit in bits:
        run = run + 1 if bit == last else 1
        last = bit
        if run == 5:
            stuffed += 1
            last, run = 1 - bit, 1
    return len(bits) + stuffed + 13  # CRC delimiter, ACK, ACK delimiter, EOF, IFS


def percentile(values, q=0.99):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)]


@dataclass
class Stream:
    count: int = 0
    last_ns: int | None = None
    max_gap_ms: float = 0.0
    gaps: deque = field(default_factory=lambda: deque(maxlen=10000))
    arrivals: deque = field(default_factory=deque)
    data: bytes = b""

    def add(self, frame: Frame):
        self.count += 1
        if self.last_ns is not None:
            gap = (frame.host_ns - self.last_ns) / 1e6
            if gap >= 0:
                self.gaps.append(gap)
                self.max_gap_ms = max(self.max_gap_ms, gap)
        self.last_ns = frame.host_ns
        self.data = frame.data
        self.arrivals.append(frame.host_ns)
        self.prune(frame.host_ns)

    def prune(self, now):
        while self.arrivals and self.arrivals[0] <= now - 1_000_000_000:
            self.arrivals.popleft()


class Statistics:
    """All live time statistics explicitly use host monotonic arrival time."""
    def __init__(self, bitrate=1_000_000):
        self.bitrate = bitrate
        self.start_ns = time.monotonic_ns()
        self.total = self.errors = self.fd_count = self.sent_count = 0
        self.streams: dict[str, Stream] = {}
        self.traffic = deque()
        self.latest = deque(maxlen=200)
        self.last_host_ns = self.start_ns

    def add(self, frame):
        self.last_host_ns = max(self.last_host_ns, frame.host_ns)
        self.total += 1
        self.latest.append(frame)
        if frame.direction == "ERR":
            self.errors += 1
            return
        self.streams.setdefault(frame.group, Stream()).add(frame)
        if frame.direction == "TX":
            self.sent_count += 1
        elif frame.fd:
            self.fd_count += 1
        else:
            self.traffic.append((frame.host_ns, classic_frame_bits(
                frame.can_id, frame.data, frame.extended, frame.remote, frame.dlc)))

    def snapshot(self, now=None):
        now = time.monotonic_ns() if now is None else now
        while self.traffic and self.traffic[0][0] <= now - 1_000_000_000:
            self.traffic.popleft()
        elapsed = max(1e-6, (now - self.start_ns) / 1e9)
        windows = {}
        for ms in (10, 100, 1000):
            bits = sum(b for t, b in self.traffic if t > now - ms * 1_000_000)
            windows[ms] = bits / (self.bitrate * min(elapsed, ms / 1000)) * 100
        rows = []
        for name, stream in sorted(self.streams.items()):
            stream.prune(now)
            rows.append({"group": name, "count": stream.count,
                         "fps": len(stream.arrivals) / min(elapsed, 1.0),
                         "last_ms": stream.gaps[-1] if stream.gaps else None,
                         "p99_ms": percentile(stream.gaps),
                         "max_ms": stream.max_gap_ms if stream.gaps else None,
                         "age_ms": max(0, (now - stream.last_ns) / 1e6),
                         "data": stream.data.hex(" ").upper()})
        return {"total": self.total, "errors": self.errors, "fd": self.fd_count,
                "tx": self.sent_count, "elapsed_s": elapsed,
                "load_percent": windows, "streams": rows}


CSV_FIELDS = ("sequence", "host_monotonic_ns", "host_unix_ns", "elapsed_s",
              "device_timestamp_raw", "channel", "direction", "can_id", "extended",
              "remote", "fd", "brs", "esi", "ack", "dlc", "data", "group")


class Capture:
    """One receiver queue; one consumer owns statistics and lossless disk writes.

    A bounded queue prevents unlimited memory use. Overflow is explicitly counted.
    The display is a snapshot, never the queue holding the full capture.
    """
    def __init__(self, path: Path | None, bitrate=1_000_000, metadata=None,
                 queue_size=100000):
        self.path = Path(path) if path else None
        self.metadata = dict(metadata or {})
        self.stats = Statistics(bitrate)
        self.queue = queue.Queue(maxsize=queue_size)
        self.lock = threading.Lock()
        self.dropped = self.accepted = self.written = 0
        self.failure = None
        self.accepting = True
        self._stop = threading.Event()
        self._file = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("x", newline="", encoding="utf-8")
            self._writer = csv.writer(self._file)
            self._writer.writerow(CSV_FIELDS)
        self.thread = threading.Thread(target=self._consume, name="can-log", daemon=True)
        self.thread.start()

    def submit(self, frame):
        if not self.accepting:
            return
        try:
            self.queue.put_nowait(frame)
            self.accepted += 1
        except queue.Full:
            self.dropped += 1

    def _consume(self):
        last_flush = time.monotonic()
        try:
            while not self._stop.is_set() or not self.queue.empty():
                batch = []
                try:
                    batch.append(self.queue.get(timeout=0.1))
                except queue.Empty:
                    pass
                for _ in range(511):
                    try:
                        batch.append(self.queue.get_nowait())
                    except queue.Empty:
                        break
                with self.lock:
                    for frame in batch:
                        self.stats.add(frame)
                        if self._file:
                            self._writer.writerow((self.written + 1, frame.host_ns,
                                frame.wall_ns, f"{(frame.host_ns-self.stats.start_ns)/1e9:.9f}",
                                "" if frame.device_timestamp is None else frame.device_timestamp,
                                frame.channel, frame.direction, f"0x{frame.can_id:X}",
                                int(frame.extended), int(frame.remote), int(frame.fd),
                                int(frame.brs), int(frame.esi), int(frame.ack), frame.dlc,
                                frame.data.hex(" ").upper(), frame.group))
                            self.written += 1
                if self._file and time.monotonic() - last_flush >= 1:
                    self._file.flush()
                    last_flush = time.monotonic()
        except Exception as exc:
            self.failure = f"记录线程失败：{exc}"
            self.accepting = False
        finally:
            if self._file:
                try:
                    self._file.flush()
                    self._file.close()
                except OSError as exc:
                    self.failure = f"日志写入失败：{exc}"

    def snapshot(self):
        with self.lock:
            result = self.stats.snapshot()
            latest = list(self.stats.latest)
        result.update(queue_dropped=self.dropped, queue_pending=self.queue.qsize(),
                      accepted=self.accepted, written=self.written, failure=self.failure)
        return result, latest

    def close(self):
        self.accepting = False  # producer must already have stopped
        self._stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("日志尚未排空，请等待写盘；未标记为完成。")
        result, _ = self.snapshot()
        if self.path:
            info = {**self.metadata, "format_version": 1, "statistics": result,
                    "bitrate": self.stats.bitrate, "completed": self.failure is None and not self.metadata.get("backend_failure"),
                    "time_basis": "host monotonic arrival; SDK timestamp stored raw, unit unverified",
                    "p99_scope": "last 10000 nonnegative intervals per stream; max is whole capture",
                    "load_basis": "successful observed classic frames incl CRC15, stuffing and IFS; excludes errors/retries/FD",
                    "loss_scope": "queue_dropped is application queue only; adapter/USB loss not proven absent"}
            self.path.with_suffix(".json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
