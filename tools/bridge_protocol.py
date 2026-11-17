"""SHARK v1 binary codec and synchronous serial client (no serial dependency)."""
from __future__ import annotations

import binascii
from collections import deque
from dataclasses import dataclass
import struct
import time

CAN_TX, STREAM_TX, ECHO = 0x01, 0x02, 0x10
INFO, MODE, ID, SAVE, CAN_TEST = range(0x20, 0x25)
ACK, CAN_RX, ECHO_REPLY, INFO_REPLY = 0x81, 0x82, 0x90, 0xA0
EXT, REQUEST_ACK, DEFAULT_ID = 1, 2, 4
MODES = ('uart-can', 'usb-can', 'usb-ttl')
WIRES = ('ascii', 'binary', 'raw')


@dataclass(frozen=True)
class Packet:
    type: int
    data: bytes = b''
    id: int = 0
    sequence: int = 0
    flags: int = 0


def cobs_encode(raw: bytes) -> bytes:
    out = bytearray([0])
    at, code = 0, 1
    for byte in raw:
        if byte == 0:
            out[at] = code
            at, code = len(out), 1
            out.append(0)
        else:
            out.append(byte)
            code += 1
            if code == 255:
                out[at] = code
                at, code = len(out), 1
                out.append(0)
    out[at] = code
    return bytes(out)


def cobs_decode(encoded: bytes) -> bytes:
    raw = bytearray()
    at = 0
    while at < len(encoded):
        code = encoded[at]
        at += 1
        if not code or at + code - 1 > len(encoded):
            raise ValueError('Malformed COBS')
        block = encoded[at:at + code - 1]
        if 0 in block:
            raise ValueError('Zero inside COBS block')
        raw.extend(block)
        at += code - 1
        if code != 255 and at < len(encoded):
            raw.append(0)
    return bytes(raw)


def encode(p: Packet) -> bytes:
    if len(p.data) > 64:
        raise ValueError('Payload exceeds 64 bytes')
    raw = struct.pack('<BBBBHI', 1, p.type, p.flags, len(p.data), p.sequence, p.id) + p.data
    raw += struct.pack('<H', binascii.crc_hqx(raw, 0xffff))
    return b'\x00\xa5' + cobs_encode(raw) + b'\x00'


def decode(encoded: bytes) -> Packet:
    """Decode the COBS body only; callers strip 00 A5 and final 00."""
    if not 1 <= len(encoded) <= 77:
        raise ValueError('Invalid encoded length')
    raw = cobs_decode(encoded)
    if len(raw) < 12:
        raise ValueError('Short packet')
    version, kind, flags, length, seq, can_id = struct.unpack_from('<BBBBHI', raw)
    if version != 1 or length > 64 or len(raw) != 12 + length:
        raise ValueError('Invalid version/length')
    if binascii.crc_hqx(raw[:-2], 0xffff) != struct.unpack_from('<H', raw, len(raw) - 2)[0]:
        raise ValueError('CRC mismatch')
    return Packet(kind, raw[10:-2], can_id, seq, flags)


class Decoder:
    """Streaming ASCII/binary mux, with the same resynchronization as firmware."""
    def __init__(self, line_limit=191):
        self.state = 0
        self.pending = bytearray()
        self.errors = 0
        self.line_limit = line_limit

    def feed(self, data: bytes) -> list[Packet | str]:
        events = []
        for byte in data:
            if byte == 0:
                if self.state == 2 and self.pending:
                    try:
                        events.append(decode(bytes(self.pending)))
                    except ValueError:
                        self.errors += 1
                elif self.state == 4:
                    self.errors += 1
                self.pending.clear()
                self.state = 1
                continue
            if self.state == 1:
                self.state = 2 if byte == 0xa5 else 0
                if self.state == 2:
                    continue
            if self.state == 2:
                if len(self.pending) < 80:
                    self.pending.append(byte)
                else:
                    self.state = 4
            elif self.state == 4:
                continue
            elif byte in (10, 13):
                if self.state == 3:
                    self.errors += 1
                elif self.pending:
                    events.append(self.pending.decode('ascii'))
                self.pending.clear()
                self.state = 0
            elif self.state == 3:
                continue
            elif byte in (8, 127):
                self.pending = self.pending[:-1]
            elif 32 <= byte < 127 and len(self.pending) < self.line_limit:
                self.pending.append(byte)
            else:
                self.state = 3
        return events


class Client:
    def __init__(self, port):
        self.port = port
        # Replies such as stats are longer than the device's command-line limit.
        self.decoder = Decoder(line_limit=1023)
        self.events = deque()
        self.sequence = 0

    def send(self, kind, data=b'', can_id=0, flags=0) -> int:
        self.sequence = (self.sequence + 1) & 0xffff
        wire = encode(Packet(kind, data, can_id, self.sequence, flags))
        if self.port.write(wire) != len(wire):
            raise OSError('Incomplete write')
        return self.sequence

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        deferred = deque()
        try:
            while time.monotonic() < deadline:
                while self.events:
                    event = self.events.popleft()
                    if predicate(event):
                        return event
                    deferred.append(event)
                    if len(deferred) > 4096:
                        deferred.popleft()
                data = self.port.read(self.port.in_waiting or 1)
                self.events.extend(self.decoder.feed(data))
            raise TimeoutError('No matching reply; use USB/USART1 in CAN mode (USART2 is raw); use the button to exit USB-TTL')
        finally:
            self.events.extendleft(reversed(deferred))

    def request(self, kind, data=b'', can_id=0, flags=0, timeout=2.0) -> Packet:
        seq = self.send(kind, data, can_id, flags)
        expected = {ECHO: ECHO_REPLY, INFO: INFO_REPLY}.get(kind, ACK)
        reply = self.wait(lambda p: isinstance(p, Packet) and p.sequence == seq and p.type == expected, timeout)
        if reply.type == ACK and (len(reply.data) != 2 or reply.data[0] != kind or reply.data[1]):
            raise OSError(f'Device rejected command: {reply.data.hex()}')
        return reply

    def command(self, text, expected='OK wire'):
        wire = (text + '\r\n').encode('ascii')
        if self.port.write(wire) != len(wire):
            raise OSError('Incomplete write')
        return self.wait(lambda p: isinstance(p, str) and p == expected)

    def info(self) -> dict:
        data = self.request(INFO).data
        if len(data) != 9 or data[0] >= 3 or any(w > 2 for w in data[5:8]) or data[8] >= 3:
            raise ValueError('Invalid INFO reply')
        return dict(mode=MODES[data[0]], id=data[1], saved_id=data[2], can_state=data[3],
                    setting=data[4], wires=list(data[5:8]), port=data[8])
