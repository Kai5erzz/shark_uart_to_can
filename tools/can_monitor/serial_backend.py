"""Legacy Damiao USB2CAN CDC protocol, including explicit single-frame sends.

Reference: official USB转CAN-linux/USB_CAN.py and AcTool/Form1.cs, can.cs.
A matching USB ID is a candidate, not proof of compatible firmware.
"""
from __future__ import annotations

import threading
import time
import struct
from collections import Counter

from .core import Frame

BITRATES = (1_000_000, 800_000, 666_000, 500_000, 400_000, 250_000,
            200_000, 125_000, 100_000, 80_000, 50_000, 40_000, 20_000,
            10_000, 5_000)
CANDIDATE_IDS = {(0x2E88, 0x4603), (0x0484, 0x5742)}
COMMANDS = {0x00, 0x01, 0x11, 0x02, 0x12, 0x03, 0x13}


def scan_serial():
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise RuntimeError("串口支持需要 pyserial：python3 -m pip install pyserial") from exc
    return [{"port": p.device, "vid": p.vid, "pid": p.pid,
             "serial_number": p.serial_number, "description": p.description,
             "candidate": (p.vid, p.pid) in CANDIDATE_IDS}
            for p in sorted(list_ports.comports(), key=lambda p: p.device)
            if p.vid is not None or p.pid is not None]


class SerialParser:
    """Reassemble 16-byte packets across USB reads and recover after noise."""
    def __init__(self):
        self.buffer = bytearray()
        self.discarded = 0
        self.malformed = 0

    def feed(self, data):
        self.buffer.extend(data)
        packets = []
        while self.buffer:
            start = self.buffer.find(b"\xaa")
            if start < 0:
                self.discarded += len(self.buffer)
                self.buffer.clear()
                break
            self.discarded += start
            del self.buffer[:start]
            if len(self.buffer) < 16:
                break
            raw = self.buffer[:16]
            cmd, flags = raw[1:3]
            can_id = int.from_bytes(raw[3:7], "little")
            valid = raw[15] == 0x55 and cmd in COMMANDS
            if cmd in (0x11, 0x12):
                valid &= (flags & 15) <= 8 and not (flags & 0x30)
                valid &= can_id <= (0x1fffffff if flags & 0x40 else 0x7ff)
            if not valid:
                self.malformed += 1
                self.discarded += 1
                del self.buffer[0]
                continue
            packets.append(bytes(raw))
            del self.buffer[:16]
        return packets


def decode_serial(raw, host_ns=None, wall_ns=None):
    flags = raw[2]
    dlc, remote = flags & 15, bool(flags & 0x80)
    return Frame(host_ns if host_ns is not None else time.monotonic_ns(),
                 wall_ns if wall_ns is not None else time.time_ns(),
                 int.from_bytes(raw[3:7], "little"),
                 b"" if remote else raw[7:7 + dlc], extended=bool(flags & 0x40),
                 remote=remote, direction="RX" if raw[1] == 0x11 else "TX", dlc=dlc)


def encode_serial_send(can_id, data, extended=False):
    if not 0 <= can_id <= (0x1fffffff if extended else 0x7ff) or len(data) > 8:
        raise ValueError('Invalid classical CAN frame')
    # Official AcTool/can.cs: packed 17-byte CAN_Function, 8 data bytes,
    # CRC_8 (reflected 0x8C, init 0) over all 30 bytes including a zero CRC slot.
    raw = bytearray(b'\x55\xaa\x1e\x01' + struct.pack('<IIBIBBBB',
        1, 1, int(extended), can_id, 0, len(data), 0, 0) + data.ljust(8, b'\0') + b'\0')
    crc = 0
    for byte in raw:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8c if crc & 1 else 0)
    raw[-1] = crc
    return bytes(raw)


class SerialBackend:
    def __init__(self, port, bitrate=1_000_000, channel=0, uart_baud=921600):
        if bitrate not in BITRATES:
            raise ValueError("此串口协议不支持所选 CAN 波特率")
        if channel != 0:
            raise ValueError("旧版串口 USB2CAN 只有 CH0")
        self.port, self.bitrate, self.uart_baud = port, bitrate, uart_baud
        self.stop = threading.Event()
        self.tx_lock = threading.Lock()
        self.failure = None
        self.parser = SerialParser()
        self.status_counts = Counter()
        self.metadata = {"backend": "Damiao legacy CDC USB2CAN", "port": port,
                         "uart_baud": uart_baud, "channel": 0,
                         "application_sends_can_frames": False,
                         "listen_only": "unverified; assume ACK participation",
                         "device_timestamp_unit": "unavailable",
                         "configuration_acknowledged": False,
                         "adapter_status_counts": {},
                         "received_serial_bytes": 0, "can_rx_count": 0,
                         "protocol_verified": False, "raw_serial_preview_hex": ""}

    def status_text(self):
        count = self.metadata["can_rx_count"]
        received = self.metadata["received_serial_bytes"]
        state = (f"已收到 {count} 帧 CAN 数据" if count else
                 f"收到 {received} 字节，但尚未识别 CAN 帧" if received else
                 "等待 CAN 数据（请确认 CAN 接线及车上电）")
        config = (f"CAN 配置应答已确认 {self.bitrate} bit/s"
                  if self.metadata["configuration_acknowledged"] else
                  f"已请求 {self.bitrate} bit/s，设备未返回配置应答")
        failures = self.status_counts['0x02']
        return f"串口已打开 {self.port} · {state} · {config}" + (f" · 适配器发送失败 {failures}" if failures else "")

    def _packets(self, data, submit):
        self.metadata["received_serial_bytes"] += len(data)
        preview = bytes.fromhex(self.metadata["raw_serial_preview_hex"])
        if len(preview) < 256 and data:
            self.metadata["raw_serial_preview_hex"] = (preview + data)[:256].hex(" ")
        for raw in self.parser.feed(data):
            cmd = raw[1]
            self.status_counts[f"0x{cmd:02X}"] += 1
            self.metadata['adapter_status_counts'] = dict(self.status_counts)
            if cmd in (0x11, 0x12):
                self.metadata["protocol_verified"] = True
                if cmd == 0x11:
                    self.metadata["can_rx_count"] += 1
                submit(decode_serial(raw))
            elif cmd == 0x13:
                self.metadata["configuration_acknowledged"] = True
                self.metadata["configured_bitrate"] = self.bitrate
            elif cmd == 0x03:
                raise RuntimeError("分析仪拒绝 CAN 波特率设置")
            # RX/TX failure statuses are adapter events, not decoded CAN error frames.

    def _read_available(self):
        # Requesting 4096 bytes on a quiet bus makes pyserial hold received bytes
        # until its 50 ms timeout. Read only available bytes (or wait for one).
        return self.serial.read(max(1, min(self.serial.in_waiting, 65536)))

    def send(self, can_id, data, extended=False):
        packet = encode_serial_send(can_id, bytes(data), extended)
        with self.tx_lock:
            if self.stop.is_set() or not hasattr(self, 'serial'):
                raise RuntimeError('CAN adapter is closed')
            if self.serial.write(packet) != len(packet):
                raise OSError('Incomplete CAN adapter write')
            self.metadata['application_sends_can_frames'] = True

    def start(self, submit):
        try:
            import serial
            self.serial = serial.Serial(self.port, self.uart_baud, timeout=.05,
                                        write_timeout=1, exclusive=True)
            self.serial.reset_input_buffer()
            # Stop any adapter-side periodic transmit retained from an earlier session.
            # No motor command or CAN transmit packet is constructed here.
            for command in (b"\x55\x03\xaa\x55",
                            bytes((0x55, 0x05, BITRATES.index(self.bitrate), 0xAA, 0x55))):
                self.serial.write(command)
                self.serial.flush()
                end = time.monotonic() + .15
                while time.monotonic() < end:
                    self._packets(self._read_available(), submit)
            # The official serial host opens the port without waiting for a bitrate
            # acknowledgment. Some firmware stays silent until CAN traffic arrives.
            # Keep listening, and keep configuration/protocol confirmation separate.
        except Exception:
            self.close()
            raise

        def worker():
            try:
                while not self.stop.is_set():
                    self._packets(self._read_available(), submit)
            except Exception as exc:
                if not self.stop.is_set():
                    self.failure = f"串口接收中断：{exc}"

        self.thread = threading.Thread(target=worker, name="damiao-cdc-rx", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=2)
        if hasattr(self, "serial"):
            self.serial.close()
        self.metadata.update(adapter_status_counts=dict(self.status_counts),
                             serial_discarded_bytes=self.parser.discarded,
                             serial_malformed_candidates=self.parser.malformed,
                             serial_incomplete_bytes=len(self.parser.buffer))
