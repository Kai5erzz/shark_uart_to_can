"""Reassemble an unframed classic CAN byte stream and optionally echo RX data."""
import threading


def ascii_text(data):
    return ''.join(chr(b) if b in (9, 10, 13) or 32 <= b < 127 else f'\\x{b:02X}' for b in data)


class RawCANStream:
    def __init__(self, limit=65536):
        self.lock = threading.Lock()
        self.limit = limit
        self.can_id = 1
        self.echo_enabled = False
        self.buffer = bytearray()
        self.frames = self.bytes = self.echo_sent = self.errors = self.revision = 0
        self.last_error = None

    def configure(self, can_id, echo_enabled):
        if not 0 <= can_id <= 0x7ff:
            raise ValueError('RAW ID必须是标准CAN ID（000～7FF，十六进制）')
        with self.lock:
            if self.can_id != can_id:
                self._clear()
            self.can_id, self.echo_enabled = can_id, echo_enabled
            self.revision += 1

    def _clear(self):
        self.buffer.clear()
        self.frames = self.bytes = self.echo_sent = self.errors = 0
        self.last_error = None
        self.revision += 1

    def clear(self):
        with self.lock:
            self._clear()

    def disable_echo(self):
        with self.lock:
            self.echo_enabled = False
            self.revision += 1

    def handle(self, frame, send):
        with self.lock:
            if (frame.direction != 'RX' or frame.extended or frame.remote or frame.fd
                    or frame.can_id != self.can_id or len(frame.data) > 8):
                return
            self.frames += 1
            self.bytes += len(frame.data)
            self.buffer.extend(frame.data)
            if len(self.buffer) > self.limit:
                del self.buffer[:-self.limit]
            echo, can_id = self.echo_enabled, self.can_id
            self.revision += 1
        if echo and frame.data:
            try:
                send(can_id, frame.data)
                with self.lock:
                    self.echo_sent += 1
                    self.revision += 1
            except Exception as exc:
                with self.lock:
                    self.errors += 1
                    self.last_error = str(exc)
                    self.revision += 1

    def snapshot(self, with_data=False):
        with self.lock:
            result = dict(can_id=self.can_id, echo_enabled=self.echo_enabled, frames=self.frames,
                          bytes=self.bytes, echo_sent=self.echo_sent, errors=self.errors,
                          last_error=self.last_error, retained_bytes=len(self.buffer),
                          display_trimmed_bytes=self.bytes - len(self.buffer), revision=self.revision)
            if with_data:
                result.update(data=bytes(self.buffer))
            return result
