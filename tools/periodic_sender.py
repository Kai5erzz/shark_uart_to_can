"""Periodic sends on the GUI event loop; late ticks skip missed deadlines."""
from __future__ import annotations

import math
import time


class PeriodicSender:
    def __init__(self, schedule, cancel, on_change=lambda: None, clock=time.monotonic):
        self.schedule = schedule
        self.cancel = cancel
        self.on_change = on_change
        self.clock = clock
        self.running = False
        self.count = 0
        self._pending = None
        self._generation = 0

    def start(self, send, interval_ms: int, limit: int = 0):
        if not isinstance(interval_ms, int) or not 1 <= interval_ms <= 3_600_000:
            raise ValueError('发送间隔必须为 1～3600000 毫秒的整数')
        if not isinstance(limit, int) or limit < 0:
            raise ValueError('发送次数必须为非负整数，0 表示持续发送')
        self.stop()
        self._send = send
        self._interval = interval_ms / 1000
        self._limit = limit
        self._deadline = self.clock()
        self.count = 0
        self.running = True
        self._generation += 1
        self.on_change()
        # Send the first message immediately; all later sends use the timer.
        self._tick(self._generation)

    def stop(self):
        self.running = False
        self._generation += 1
        if self._pending is not None:
            self.cancel(self._pending)
            self._pending = None
        self.on_change()

    def _tick(self, generation):
        if not self.running or generation != self._generation:
            return
        self._pending = None
        try:
            ok = self._send()
        except Exception:
            self.stop()
            raise
        # A failed serial write can disconnect and stop us inside _send().
        if not self.running or generation != self._generation:
            return
        if not ok:
            self.stop()
            return
        self.count += 1
        if self._limit and self.count >= self._limit:
            self.stop()
            return
        self.on_change()
        if not self.running or generation != self._generation:
            return
        self._deadline += self._interval
        now = self.clock()
        if self._deadline <= now:
            self._deadline += (math.floor((now - self._deadline) / self._interval) + 1) * self._interval
        delay_ms = max(1, math.ceil((self._deadline - now) * 1000))
        self._pending = self.schedule(delay_ms, lambda: self._tick(generation))
