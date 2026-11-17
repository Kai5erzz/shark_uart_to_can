"""Opt-in classic CAN request/reply responder for SHARK RTT tests."""
import threading
import time


class EchoResponder:
    def __init__(self):
        self.lock = threading.Lock()
        self.enabled = False
        self.request_id, self.reply_id = 0x6e0, 0x6e1
        self.received = self.sent = self.errors = 0
        self.last_error = None
        self.max_service_us = 0.0

    def configure(self, enabled, request_id=None, reply_id=None):
        with self.lock:
            request_id = self.request_id if request_id is None else request_id
            reply_id = self.reply_id if reply_id is None else reply_id
            if not 0 <= request_id <= 0x7ff or not 0 <= reply_id <= 0x7ff or request_id == reply_id:
                raise ValueError('请求/回复 ID 必须为不同的标准 CAN ID (0..7FF)')
            self.enabled, self.request_id, self.reply_id = enabled, request_id, reply_id

    def handle(self, frame, send):
        with self.lock:
            if (not self.enabled or frame.direction != 'RX' or frame.extended or frame.remote
                    or frame.fd or frame.can_id != self.request_id or len(frame.data) != 8):
                return
            reply_id = self.reply_id
            self.received += 1
        started = time.monotonic_ns()
        try:
            send(reply_id, frame.data)
            with self.lock:
                self.sent += 1
                self.max_service_us = max(self.max_service_us, (time.monotonic_ns() - started) / 1000)
        except Exception as exc:
            with self.lock:
                self.errors += 1
                self.last_error = str(exc)

    def snapshot(self):
        with self.lock:
            return dict(enabled=self.enabled, request_id=self.request_id, reply_id=self.reply_id,
                        received=self.received, sent=self.sent, errors=self.errors,
                        last_error=self.last_error, max_service_us=self.max_service_us)
