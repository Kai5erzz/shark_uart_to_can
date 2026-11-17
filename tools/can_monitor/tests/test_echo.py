import ctypes
from dataclasses import replace
import os
import struct
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.can_monitor.backends import NativeBackend, SocketBackend
from tools.can_monitor.core import Frame
from tools.can_monitor.echo import EchoResponder
from tools.can_monitor.serial_backend import SerialBackend, encode_serial_send


class EchoTests(unittest.TestCase):
    def test_opt_in_and_filters_prevent_echo_loops(self):
        echo, send = EchoResponder(), Mock()
        request = Frame(1, 2, 0x6e0, bytes(range(8)))
        echo.handle(request, send)
        send.assert_not_called()
        echo.configure(True)
        for changes in (dict(direction='TX'), dict(direction='ERR'), dict(extended=True),
                        dict(remote=True), dict(fd=True), dict(can_id=0x6e1),
                        dict(data=b'short')):
            echo.handle(replace(request, **changes), send)
        send.assert_not_called()
        echo.handle(request, send)
        send.assert_called_once_with(0x6e1, bytes(range(8)))
        self.assertEqual((echo.snapshot()['received'], echo.snapshot()['sent']), (1, 1))
        echo.configure(False)
        echo.handle(request, send)
        self.assertEqual(send.call_count, 1)
        with self.assertRaises(ValueError):
            echo.configure(True, 0x100, 0x100)
        echo.configure(True, 0x500, 0x501)
        echo.configure(False)
        self.assertEqual((echo.snapshot()['request_id'], echo.snapshot()['reply_id']), (0x500, 0x501))

    def test_failed_write_counted_without_breaking_capture(self):
        echo = EchoResponder()
        echo.configure(True)
        echo.handle(Frame(1, 2, 0x6e0, b'12345678'), Mock(side_effect=OSError('unplugged')))
        self.assertEqual(echo.snapshot()['errors'], 1)
        self.assertEqual(echo.snapshot()['sent'], 0)
        self.assertIn('unplugged', echo.snapshot()['last_error'])

    def test_official_30_byte_serial_send_fixture_and_short_write(self):
        # Packed CAN_Function fixture, CRC8/MAXIM over 30 bytes with zero CRC slot.
        fixture = bytes.fromhex('55aa1e01010000000100000000e1060000000800000001020304050607d1')
        self.assertEqual(encode_serial_send(0x6e1, bytes(range(8))), fixture)
        backend = SerialBackend('/dev/mock')
        backend.serial = Mock()
        backend.serial.write.return_value = 30
        backend.send(0x6e1, bytes(range(8)))
        backend.serial.write.assert_called_once_with(fixture)
        self.assertTrue(backend.metadata['application_sends_can_frames'])
        backend.serial.write.return_value = 29
        with self.assertRaises(OSError):
            backend.send(0x6e1, bytes(range(8)))
        backend.stop.set()
        with self.assertRaises(RuntimeError):
            backend.send(0x6e1, bytes(range(8)))
        for can_id, data in ((0x800, b''), (-1, b''), (1, b'x' * 9)):
            with self.assertRaises(ValueError):
                encode_serial_send(can_id, data)

    def test_socketcan_wire_layout_and_failure(self):
        backend = SocketBackend()
        backend.sock = Mock()
        backend.sock.send.return_value = 16
        backend.send(0x6e1, b'12345678')
        backend.sock.send.assert_called_once_with(struct.pack('=IB3x8s', 0x6e1, 8, b'12345678'))
        backend.send(0x123456, b'1', extended=True)
        self.assertEqual(backend.sock.send.call_args.args[0], struct.pack('=IB3x8s', 0x80123456, 1, b'1'))
        backend.sock.send.return_value = 0
        with self.assertRaises(OSError):
            backend.send(1, b'')

    def test_native_sdk_abi_and_failure(self):
        backend = NativeBackend.__new__(NativeBackend)
        function = Mock(return_value=True)
        backend.lib = SimpleNamespace(dmcan_device_send_can=function)
        backend.device, backend.channel, backend.metadata = ctypes.c_void_p(1), 0, {}
        backend.send(0x6e1, b'12345678')
        args = function.call_args.args
        self.assertEqual(args[1:8], (0, 0x6e1, False, False, False, False, 8))
        self.assertEqual(bytes(args[-1]), b'12345678')
        self.assertEqual(len(function.argtypes), 9)
        function.return_value = False
        with self.assertRaises(OSError):
            backend.send(1, b'')


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk integration needs xvfb-run')
class EchoGUIIntegrationTest(unittest.TestCase):
    def test_enable_callback_disconnect_and_layout(self):
        import tkinter as tk
        from tools.can_monitor.app import App
        class Backend:
            metadata = {'backend': 'simulator'}
            failure = None
            def start(self, submit): self.submit = submit
            def send(self, can_id, data): self.sent.append((can_id, data))
            def close(self): pass
            def __init__(self): self.sent = []
        root = tk.Tk()
        backend = Backend()
        def wait_for(predicate):
            end = time.monotonic() + 3
            def poll():
                if predicate() or time.monotonic() >= end:
                    root.quit()
                else:
                    root.after(5, poll)
            root.after(5, poll)
            root.mainloop()
            self.assertTrue(predicate())
        try:
            with patch('tools.can_monitor.app.discover', return_value={'native': [], 'ports': []}), \
                 patch('tools.can_monitor.app.SerialBackend', return_value=backend):
                app = App(root)
                wait_for(lambda: not app.busy)
                app.backend_choice.set('达妙串口 USB2CAN')
                app.port.set('/dev/mock')
                app.record.set(False)
                app.connect()
                wait_for(lambda: app.backend is not None)
                app.echo_enabled.set(True)
                app.configure_echo()
                backend.submit(Frame(1, 2, 0x6e0, b'12345678'))
                self.assertEqual(backend.sent, [(0x6e1, b'12345678')])
                root.update_idletasks()
                self.assertGreater(app.tree.winfo_height(), 80)
                app.disconnect()
                wait_for(lambda: app.backend is None)
                self.assertFalse(app.echo.snapshot()['enabled'])
                self.assertEqual(app.capture.metadata['echo']['sent'], 1)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
