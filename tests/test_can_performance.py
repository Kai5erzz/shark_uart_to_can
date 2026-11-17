"""Benchmark correctness with controlled arrivals, plus Tk/PTY ownership checks."""
import heapq
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from bridge_protocol import (ACK, CAN_RX, CAN_TEST, CAN_TX, ECHO, ECHO_REPLY,
                             INFO, INFO_REPLY, Client, Decoder, Packet, encode)
from can_performance import Config, export, run


class SimulatedPort:
    """Serial-shaped transport with a virtual clock and scheduled responses."""
    def __init__(self, behavior=None):
        self.timeout = .05
        self.port, self.baudrate = 'simulated', 921600
        self.now = 1_000_000_000
        self.queue, self.serial = [], 0
        self.buffer = bytearray()
        self.decoder = Decoder()
        self.state = dict(can=2, wire=0)
        self.commands = []
        self.behavior = behavior

    def schedule(self, value, delay_ms=0):
        raw = encode(value) if isinstance(value, Packet) else (value + '\r\n').encode()
        self.serial += 1
        heapq.heappush(self.queue, (self.now + int(delay_ms * 1e6), self.serial, raw))

    def collect(self):
        while self.queue and self.queue[0][0] <= self.now:
            self.buffer.extend(heapq.heappop(self.queue)[2])

    @property
    def in_waiting(self):
        self.collect()
        return len(self.buffer)

    def reset_input_buffer(self):
        self.buffer.clear()

    def read(self, size):
        self.collect()
        if not self.buffer:
            end = self.now + max(1000, int(self.timeout * 1e9))
            self.now = min(end, self.queue[0][0]) if self.queue else end
            self.collect()
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def write(self, data):
        self.now += 1000
        for cmd in self.decoder.feed(data):
            self.commands.append(cmd)
            if isinstance(cmd, Packet):
                if cmd.type == INFO:
                    self.schedule(Packet(INFO_REPLY, bytes([1, 1, 1, self.state['can'], 0,
                        0, 0, self.state['wire'], 2]), sequence=cmd.sequence))
                elif cmd.type == CAN_TEST:
                    self.state['can'] = cmd.data[0]
                    self.schedule(Packet(ACK, bytes([CAN_TEST, 0]), sequence=cmd.sequence))
                elif self.behavior:
                    self.behavior(self, cmd)
                else:
                    self.schedule(Packet(CAN_RX if cmd.type == CAN_TX else ECHO_REPLY,
                                         cmd.data, 0x6e1, cmd.sequence), 1)
            elif cmd.startswith('wire '):
                self.state['wire'] = ('ascii', 'binary', 'raw').index(cmd[5:])
                self.schedule('OK wire')
            elif cmd == 'stats':
                # The first real firmware stats reply exceeds its command line limit.
                self.schedule('mode=usb-can ' + 'counter=12345 ' * 30)
                for index in range(3):
                    self.schedule(f'port={index} errors=0')
        return len(data)


class PerformanceTest(unittest.TestCase):
    def execute(self, port, config, **kwargs):
        with patch('can_performance.time.monotonic_ns', side_effect=lambda: port.now):
            return run(port, config, **kwargs)

    def test_can_and_echo_restore_and_export(self):
        for target in ('can', 'serial'):
            port = SimulatedPort()
            result = self.execute(port, Config(target=target, rounds=20, warmup=3, interval_ms=2, window=4))
            self.assertIsNone(result['error'])
            self.assertFalse(result['restore_errors'])
            self.assertEqual(result['summary']['received'], 20)
            self.assertEqual(result['summary']['missing'], 0)
            self.assertAlmostEqual(result['summary']['p99_ms'], 1.001, places=3)
            self.assertEqual(len(result['records']), 23)
            self.assertEqual(len(result['before_stats']), 4)
            self.assertEqual(port.state, dict(can=2, wire=0))
            self.assertEqual(port.timeout, .05)
            self.assertFalse(any(isinstance(c, Packet) and c.type in (0x21, 0x22, 0x23)
                                 for c in port.commands))
            with tempfile.TemporaryDirectory() as folder:
                path = export(result, Path(folder) / 'sample.json')
                self.assertEqual(json.loads(path.read_text())['summary']['received'], 20)
                self.assertEqual(len(path.with_suffix('.csv').read_text().splitlines()), 24)

    def test_mismatch_duplicate_late_rejection_and_out_of_order(self):
        def behavior(port, cmd):
            seq = struct.unpack_from('<I', cmd.data, 4)[0]
            reply = Packet(CAN_RX, cmd.data, 0x6e1)
            if seq == 1:
                port.schedule(Packet(CAN_RX, cmd.data, 0x123))
                port.schedule(Packet(CAN_RX, cmd.data, 0x6e1, flags=1))
                port.schedule(Packet(CAN_RX, b'old!' + cmd.data[4:], 0x6e1))
                port.schedule(reply, 3)
                port.schedule(reply, 4)
            elif seq == 2:
                port.schedule(reply, 1)
            elif seq == 3:
                port.schedule(Packet(ACK, bytes([CAN_TX, 3]), sequence=cmd.sequence))
            elif seq == 4:
                port.schedule(reply, 25)  # after its 20ms timeout, while #5 is pending
            else:
                port.schedule(reply, 19)
        port = SimulatedPort(behavior)
        result = self.execute(port, Config(rounds=6, warmup=0, interval_ms=5, timeout_ms=20, window=4))
        summary = result['summary']
        self.assertIsNone(result['error'])
        self.assertEqual((summary['received'], summary['rejected'], summary['timeouts']), (4, 1, 1))
        self.assertEqual((summary['duplicates'], summary['late'], summary['unrelated']), (1, 1, 1))
        self.assertEqual(summary['missing'], 2)
        self.assertEqual(port.state, dict(can=2, wire=0))

    def test_cancel_and_no_echo_restore(self):
        cancel = threading.Event()
        def cancel_write(port, cmd):
            cancel.set()
        port = SimulatedPort(cancel_write)
        result = self.execute(port, Config(rounds=10, warmup=0), cancel=cancel)
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['summary']['cancelled'], 1)
        self.assertEqual(port.state, dict(can=2, wire=0))
        port = SimulatedPort(lambda *_: None)
        result = self.execute(port, Config(warmup=2, timeout_ms=5))
        self.assertIn('预热没有收到回传', result['error'])
        self.assertEqual(result['summary']['sent'], 0)
        self.assertEqual(port.state, dict(can=2, wire=0))

    def test_long_stats_host_decoder_keeps_firmware_limit(self):
        line = 'mode=usb-can ' + 'x' * 400
        strict = Decoder()
        self.assertEqual(strict.feed((line + '\n').encode()), [])
        self.assertEqual(strict.errors, 1)
        port = SimulatedPort()
        port.schedule(line)
        self.assertEqual(Client(port).wait(lambda p: isinstance(p, str)), line)

    def test_keyboard_interrupt_restores_configuration(self):
        def interrupt(*_):
            raise KeyboardInterrupt
        port = SimulatedPort(interrupt)
        result = self.execute(port, Config(rounds=10, warmup=0))
        self.assertTrue(result['cancelled'])
        self.assertIsNone(result['error'])
        self.assertEqual(port.state, dict(can=2, wire=0))


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk integration needs xvfb-run')
class PerformanceGUIIntegrationTest(unittest.TestCase):
    def test_completion_cancel_and_disconnect_restore_serial_ownership(self):
        import pty
        import select
        import time
        import tkinter as tk
        from tkinter import ttk
        from types import SimpleNamespace
        import serial_assistant
        from performance_ui import PerformancePanel

        master, slave = pty.openpty()
        stopped = threading.Event()
        simulator = SimulatedPort()
        failures, notices = [], []
        def board():
            while not stopped.is_set():
                if select.select([master], [], [], .005)[0]:
                    simulator.write(os.read(master, 4096))
                simulator.now += 10_000_000
                simulator.collect()
                if simulator.buffer:
                    os.write(master, bytes(simulator.buffer))
                    simulator.buffer.clear()
        worker = threading.Thread(target=board, daemon=True)
        worker.start()
        root = tk.Tk()
        def widgets(parent):
            return [child for widget in parent.winfo_children() for child in [widget, *widgets(widget)]]
        def button(text):
            return next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget('text') == text)
        def check(action):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
                root.destroy()
        deadline = time.monotonic() + 12
        def await_condition(predicate, action):
            if predicate():
                check(action)
            elif time.monotonic() > deadline:
                check(lambda: self.fail('GUI performance operation did not finish'))
            else:
                root.after(20, lambda: await_condition(predicate, action))
        def setup():
            button('连接').invoke()
            panel = next(w for w in widgets(root) if isinstance(w, PerformancePanel))
            notebook = panel.master
            notebook.select(panel)
            root.update_idletasks()
            self.assertGreater(panel.canvas.winfo_height(), 80)
            self.assertLessEqual(panel.export_button.winfo_rootx() + panel.export_button.winfo_width(),
                                 root.winfo_rootx() + root.winfo_width())
            panel.values['rounds'].set('5')
            panel.start_button.invoke()
            await_condition(lambda: panel.result is not None and not panel.running, lambda: completed(panel))
        def completed(panel):
            self.assertIsNone(panel.result['error'])
            self.assertEqual(panel.result['summary']['received'], 5)
            self.assertEqual(simulator.state, dict(can=2, wire=0))
            # A new run must own the port again, then disconnect cancels and restores.
            panel.values['rounds'].set('10000')
            panel.start_button.invoke()
            root.after(100, lambda: button('断开').invoke())
            await_condition(lambda: not panel.running and button('连接').cget('text') == '连接',
                            lambda: cancelled(panel))
        def cancelled(panel):
            self.assertTrue(panel.result['cancelled'])
            self.assertEqual(simulator.state, dict(can=2, wire=0))
            self.assertFalse(notices, notices)
            root.destroy()
        try:
            with patch('tkinter.Tk', return_value=root), \
                 patch.object(serial_assistant.list_ports, 'comports',
                              return_value=[SimpleNamespace(device=os.ttyname(slave))]), \
                 patch('tkinter.messagebox.showerror', side_effect=lambda *args: notices.append(args)):
                root.after(50, lambda: check(setup))
                serial_assistant.gui()
            if failures:
                raise failures[0]
        finally:
            stopped.set()
            worker.join(timeout=1)
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
