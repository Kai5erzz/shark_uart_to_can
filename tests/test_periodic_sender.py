"""Deterministic timer tests, plus real Tk + serial PTY integration under Xvfb."""
import heapq
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from periodic_sender import PeriodicSender


class Scheduler:
    def __init__(self):
        self.now = 0.0
        self.pending = []
        self.cancelled = set()
        self.serial = 0

    def schedule(self, milliseconds, callback):
        self.serial += 1
        heapq.heappush(self.pending, (self.now + milliseconds / 1000, self.serial, callback))
        return self.serial

    def cancel(self, token):
        self.cancelled.add(token)

    def advance(self, seconds):
        end = self.now + seconds
        while self.pending and self.pending[0][0] <= end:
            deadline, token, callback = heapq.heappop(self.pending)
            self.now = max(self.now, deadline)
            if token not in self.cancelled:
                callback()
        self.now = max(self.now, end)

    def sender(self):
        return PeriodicSender(self.schedule, self.cancel, clock=lambda: self.now)


class PeriodicSenderTest(unittest.TestCase):
    def test_exact_limit_and_first_send_immediate(self):
        scheduler = Scheduler()
        sender = scheduler.sender()
        calls = []
        sender.start(lambda: calls.append(scheduler.now) or True, 100, 3)
        self.assertEqual(calls, [0])
        scheduler.advance(1)
        self.assertEqual(len(calls), 3)
        self.assertAlmostEqual(calls[1], .1, places=3)
        self.assertAlmostEqual(calls[2], .2, places=3)
        self.assertEqual(sender.count, 3)
        self.assertFalse(sender.running)

    def test_stop_and_restart_discard_old_callbacks(self):
        scheduler = Scheduler()
        sender = scheduler.sender()
        calls = []
        sender.start(lambda: calls.append('old') or True, 100)
        stale_callback = scheduler.pending[0][2]
        sender.stop()
        scheduler.advance(.5)
        self.assertEqual(calls, ['old'])
        sender.start(lambda: calls.append('new') or True, 50, 2)
        stale_callback()
        scheduler.advance(.2)
        self.assertEqual(calls, ['old', 'new', 'new'])
        self.assertEqual(sender.count, 2)

    def test_failed_or_disconnecting_write_stops(self):
        for disconnect in (False, True):
            scheduler = Scheduler()
            sender = scheduler.sender()
            calls = []
            def write():
                calls.append(1)
                if len(calls) == 2:
                    if disconnect:
                        sender.stop()
                    return False
                return True
            sender.start(write, 20)
            scheduler.advance(1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(sender.count, 1)
            self.assertFalse(sender.running)

    def test_event_loop_delay_skips_missed_sends(self):
        scheduler = Scheduler()
        sender = scheduler.sender()
        calls = []
        sender.start(lambda: calls.append(scheduler.now) or True, 10)
        # Simulate a busy GUI resuming half a second after the due time.
        scheduler.now = .5
        scheduler.advance(0)
        self.assertEqual(calls, [0, .5])
        self.assertEqual(sender.count, 2)
        self.assertGreater(scheduler.pending[0][0], .5)
        sender.stop()

    def test_bad_settings_send_nothing(self):
        scheduler = Scheduler()
        sender = scheduler.sender()
        for interval, count in ((0, 0), (-1, 0), (3_600_001, 0), (1.5, 0), (100, -1)):
            with self.assertRaises(ValueError):
                sender.start(lambda: self.fail('Invalid settings transmitted bytes'), interval, count)
        self.assertFalse(sender.running)


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk integration needs a display; run under xvfb-run')
class TimerGUIIntegrationTest(unittest.TestCase):
    def test_text_hex_binary_and_disconnect_over_real_serial(self):
        import pty
        import select
        import threading
        import tkinter as tk
        from tkinter import ttk
        from types import SimpleNamespace
        from unittest.mock import patch
        import serial_assistant
        from bridge_protocol import Decoder, CAN_TX, Packet

        master, slave = pty.openpty()
        root = tk.Tk()
        stopped = threading.Event()
        wire = bytearray()
        failures = []
        notices = []

        def receive():
            while not stopped.is_set():
                if select.select([master], [], [], .01)[0]:
                    try:
                        wire.extend(os.read(master, 4096))
                    except OSError:
                        return
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()

        def widgets(parent):
            return [child for widget in parent.winfo_children()
                    for child in [widget, *widgets(widget)]]

        def button(text):
            return next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget('text') == text)

        def set_entry(widget, value):
            root.setvar(widget.cget('textvariable'), value)

        def check(action):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
                root.destroy()

        def setup():
            button('连接').invoke()
            bottom = button('发送').master
            text_entry = next(w for w in bottom.winfo_children() if isinstance(w, ttk.Entry))
            timer = button('开始定时').master
            source = next(w for w in timer.winfo_children() if isinstance(w, ttk.Combobox))
            interval, limit = [w for w in timer.winfo_children() if type(w) is ttk.Entry]
            set_entry(text_entry, 'send 123 11223344')
            set_entry(interval, '15')
            set_entry(limit, '3')
            button('开始定时').invoke()

            def verify_text():
                self.assertEqual(bytes(wire), b'send 123 11223344\r\n' * 3)
                self.assertEqual(button('开始定时').cget('text'), '开始定时')
                wire.clear()
                set_entry(text_entry, '00 0D 0A FF')
                for caption in ('HEX发送', '透明/原始流'):
                    next(w for w in bottom.winfo_children() if isinstance(w, ttk.Checkbutton)
                         and w.cget('text') == caption).invoke()
                set_entry(limit, '2')
                button('开始定时').invoke()
                root.after(150, lambda: check(verify_hex))

            def verify_hex():
                self.assertEqual(bytes(wire), bytes.fromhex('00 0D 0A FF') * 2)
                wire.clear()
                next(w for w in bottom.winfo_children() if isinstance(w, ttk.Checkbutton)
                     and w.cget('text') == '透明/原始流').invoke()
                source.set('二进制 CAN')
                set_entry(limit, '3')
                button('开始定时').invoke()
                root.after(150, lambda: check(verify_binary))

            def verify_binary():
                packets = Decoder().feed(bytes(wire))
                self.assertEqual(len(packets), 3)
                self.assertTrue(all(isinstance(p, Packet) and p.type == CAN_TX
                                    and p.id == 0x123 and p.data == bytes.fromhex('00 0D 0A A5 FF')
                                    for p in packets))
                self.assertEqual([p.sequence for p in packets], [1, 2, 3])
                wire.clear()
                set_entry(limit, '0')
                set_entry(interval, '200')
                button('开始定时').invoke()
                button('断开').invoke()
                self.assertEqual(button('开始定时').cget('text'), '开始定时')
                root.after(100, lambda: check(after_disconnect))

            def after_disconnect():
                self.assertEqual(len(Decoder().feed(bytes(wire))), 1)
                wire.clear()
                root.after(300, lambda: check(done))

            def done():
                self.assertEqual(bytes(wire), b'')
                self.assertEqual(notices, [])
                root.destroy()

            root.after(150, lambda: check(verify_text))

        root.after(20, lambda: check(setup))
        root.after(5000, lambda: check(lambda: self.fail('GUI timer test timed out')))
        try:
            with patch.object(tk, 'Tk', return_value=root), \
                 patch.object(serial_assistant.list_ports, 'comports',
                              return_value=[SimpleNamespace(device=os.ttyname(slave))]), \
                 patch('tkinter.messagebox.showerror', side_effect=lambda *a: notices.append(a)), \
                 patch('tkinter.messagebox.showinfo', side_effect=lambda *a: notices.append(a)):
                serial_assistant.gui()
            if failures:
                raise failures[0]
        finally:
            stopped.set()
            reader.join(timeout=1)
            os.close(master)
            os.close(slave)
            try:
                root.destroy()
            except tk.TclError:
                pass


if __name__ == '__main__':
    unittest.main()
