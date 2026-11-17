import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
MONITOR_ROOT = ROOT if (ROOT / 'tools/can_monitor/app.py').is_file() else ROOT.parent / 'code_914/HNU_Embedded_Framework'
sys.path.insert(0, str(ROOT / 'tools'))
from raw_serial_test import Config, export, long_text, run


class EchoPort:
    def __init__(self, mode='echo', cancel=None):
        self.timeout, self.baudrate, self.port = .05, 921600, 'simulated UART2'
        self.mode, self.cancel = mode, cancel
        self.queue, self.written = bytearray(), bytearray()

    @property
    def in_waiting(self):
        return len(self.queue)

    def reset_input_buffer(self):
        self.queue.clear()

    def write(self, data):
        self.written.extend(data)
        if self.mode != 'silent':
            self.queue.extend(data)
        if self.mode == 'corrupt':
            self.queue[-1] ^= 1
        if self.mode == 'extra':
            self.queue.extend(b'!')
        if self.cancel:
            self.cancel.set()
        return len(data)

    def read(self, size):
        size = min(size, 3)  # split CAN/serial transfers at unrelated boundaries
        result = bytes(self.queue[:size])
        del self.queue[:size]
        if not result:
            time.sleep(min(self.timeout, .001))
        return result


class RawSerialTest(unittest.TestCase):
    def test_long_ascii_exact_roundtrip_and_exports(self):
        payload = long_text(16384).encode('ascii')
        port = EchoPort()
        result = run(port, Config(payload, chunk_size=17, interval_ms=0))
        self.assertTrue(result['passed'], result)
        self.assertEqual(bytes(port.written), payload)  # no commands or additional newline
        self.assertEqual(result['sha256_sent'], result['sha256_received'])
        self.assertEqual((result['sent'], result['received']), (16384, 16384))
        self.assertEqual(port.timeout, .05)
        with tempfile.TemporaryDirectory() as folder:
            path = export(result, Path(folder) / 'raw.json')
            self.assertTrue(json.loads(path.read_text())['passed'])
            self.assertEqual(path.with_suffix('.tx.txt').read_bytes(), payload)
            self.assertEqual(path.with_suffix('.rx.txt').read_bytes(), payload)

    def test_corruption_and_extra_bytes_fail_with_offset(self):
        for mode, offset in [('corrupt', 7), ('extra', 8)]:
            result = run(EchoPort(mode), Config(b'12345678', interval_ms=0))
            self.assertFalse(result['passed'])
            self.assertEqual(result['mismatch_offset'], offset)

    def test_timeout_limits_outstanding_and_cancel_restores_timeout(self):
        port = EchoPort('silent')
        result = run(port, Config(b'x' * 100, interval_ms=0, window_bytes=32, timeout_ms=20))
        self.assertFalse(result['passed'])
        self.assertIn('超时', result['error'])
        self.assertEqual(result['sent'], 32)
        cancel = threading.Event()
        port = EchoPort(cancel=cancel)
        result = run(port, Config(b'x' * 100, interval_ms=0), cancel)
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['passed'])
        self.assertEqual(port.timeout, .05)


@unittest.skipUnless(os.environ.get('DISPLAY') and (MONITOR_ROOT / 'tools/can_monitor/app.py').is_file(),
                     'Needs Xvfb and the CAN Monitor reference project')
class BothGUIRawIntegrationTest(unittest.TestCase):
    def test_longtext_roundtrip_and_independent_reverse_send(self):
        import pty
        import select
        import tkinter as tk
        from tkinter import ttk
        from types import SimpleNamespace
        from unittest.mock import patch
        import serial_assistant
        from raw_test_ui import RawTestPanel
        sys.path.insert(0, str(MONITOR_ROOT))
        from tools.can_monitor.app import App
        from tools.can_monitor.core import Frame

        master, slave = pty.openpty()
        stopped = threading.Event()
        wire = bytearray()
        class CANBridge:
            def __init__(self):
                self.metadata = {'backend': 'PTY CAN bridge simulation'}
                self.failure = None
            def start(self, submit):
                self.submit = submit
                def worker():
                    while not stopped.is_set():
                        if select.select([master], [], [], .005)[0]:
                            data = os.read(master, 4096)
                            wire.extend(data)
                            for offset in range(0, len(data), 8):
                                submit(Frame(time.monotonic_ns(), time.time_ns(), 1, data[offset:offset+8]))
                self.thread = threading.Thread(target=worker, daemon=True)
                self.thread.start()
            def send(self, can_id, data):
                if can_id == 1:
                    os.write(master, data)
                self.submit(Frame(time.monotonic_ns(), time.time_ns(), can_id, data, direction='TX'))
            def close(self):
                stopped.set()
                self.thread.join(timeout=1)
        root = tk.Tk()
        monitor_window = tk.Toplevel(root)
        failures, notices = [], []
        backend = CANBridge()
        def widgets(parent):
            return [child for widget in parent.winfo_children() for child in [widget, *widgets(widget)]]
        def button(text):
            return next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget('text') == text)
        def cleanup():
            for token in root.tk.call('after', 'info'):
                root.after_cancel(token)
            root.destroy()
        def check(action):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
                cleanup()
        deadline = time.monotonic() + 12
        def wait_for(predicate, action):
            if predicate():
                check(action)
            elif time.monotonic() > deadline:
                check(lambda: self.fail('RAW GUI test did not finish'))
            else:
                root.after(10, lambda: wait_for(predicate, action))
        payload = long_text(4096)
        reverse = long_text(1024).replace('SHARK', 'CANRX')
        def setup():
            button('连接').invoke()
            monitor.backend_choice.set('达妙串口 USB2CAN')
            monitor.port.set('/dev/simulated')
            monitor.record.set(False)
            monitor.connect()
            wait_for(lambda: monitor.backend is not None, start_raw)
        def start_raw():
            monitor.raw_panel.echo_enabled.set(True)
            monitor.raw_panel.configure()
            monitor.pages.select(monitor.raw_panel)
            panel = next(w for w in widgets(root) if isinstance(w, RawTestPanel))
            panel.master.select(panel)
            panel.source.delete('1.0', 'end')
            panel.source.insert('1.0', payload)
            panel.values['interval'].set('0')
            panel.start_button.invoke()
            wait_for(lambda: panel.result is not None and not panel.running, lambda: verify_roundtrip(panel))
        def verify_roundtrip(panel):
            self.assertTrue(panel.result['passed'], panel.result)
            self.assertEqual(bytes(wire), payload.encode())
            state = monitor.raw_stream.snapshot(with_data=True)
            self.assertEqual(state['data'], payload.encode())
            self.assertEqual(state['echo_sent'], state['frames'])
            monitor.raw_panel.refresh()
            self.assertEqual(monitor.raw_panel.received.get('1.0', 'end-1c'), payload)
            self.assertGreater(monitor.raw_panel.received.winfo_height(), 40)
            backend.metadata['adapter_status_counts'] = {'0x02': 1}
            monitor.raw_panel.refresh()  # Adapter failure must update even without new CAN RX.
            self.assertIn('适配器发送失败 1', monitor.raw_panel.status.get())
            monitor.raw_panel.echo_enabled.set(False)
            monitor.raw_panel.configure()
            monitor.raw_panel.source.delete('1.0', 'end')
            monitor.raw_panel.source.insert('1.0', reverse)
            monitor.raw_panel.interval.set('1')
            monitor.raw_panel.send_button.invoke()
            wait_for(lambda: not monitor.raw_panel.sending and panel.passive_count == len(reverse),
                     lambda: verify_reverse(panel))
        def verify_reverse(panel):
            self.assertEqual(bytes(panel.passive), reverse.encode())
            self.assertEqual(bytes(wire), payload.encode())  # no TX echo loop
            panel.source.delete('1.0', 'end')
            panel.source.insert('1.0', reverse)
            panel.compare_button.invoke()
            self.assertTrue(panel.status.get().startswith('PASS：CAN→USART2'))
            panel.values['interval'].set('30')
            panel.start_button.invoke()
            root.after(50, lambda: button('断开').invoke())
            wait_for(lambda: not panel.running and button('连接').cget('text') == '连接',
                     lambda: finish(panel))
        def finish(panel):
            self.assertTrue(panel.result['cancelled'])
            self.assertFalse(notices, notices)
            monitor.close()
            wait_for(lambda: monitor.closed, cleanup)
        try:
            with patch('tkinter.Tk', return_value=root), \
                 patch.object(serial_assistant.list_ports, 'comports', return_value=[SimpleNamespace(device=os.ttyname(slave))]), \
                 patch('tools.can_monitor.app.App.scan'), \
                 patch('tools.can_monitor.app.SerialBackend', return_value=backend), \
                 patch('tkinter.messagebox.showerror', side_effect=lambda *args: notices.append(args)):
                monitor = App(monitor_window)
                root.after(80, lambda: check(setup))
                serial_assistant.gui()
            if failures:
                raise failures[0]
        finally:
            stopped.set()
            if hasattr(backend, 'thread'):
                backend.thread.join(timeout=1)
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
