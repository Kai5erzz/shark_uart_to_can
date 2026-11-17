import ctypes as C
import csv
import json
import queue
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.can_monitor.backends import (BaudInfo, DemoBackend, NativeBackend,
    RxFrame, RxHead, SocketBackend, decode_sdk)
from tools.can_monitor.core import Capture, Frame, Statistics, classic_frame_bits


class MonitorTests(unittest.TestCase):
    def test_sdk_wire_layout_and_flags(self):
        # External packed layout from dmcan.h, not constructed through ctypes.
        payload = bytes(range(12))
        packet = struct.pack("<IQBBH64s", 0x40000123, 123456789, 1, 0x9D, 0, payload)
        raw = RxFrame.from_buffer_copy(packet)
        frame = decode_sdk(raw, host_ns=100, wall_ns=200)
        self.assertEqual((C.sizeof(RxHead), C.sizeof(BaudInfo)), (16, 18))
        self.assertEqual((frame.can_id, frame.channel, frame.device_timestamp), (0x123, 1, 123456789))
        self.assertTrue(frame.extended and frame.fd and frame.brs and frame.ack)
        self.assertEqual(frame.data, payload)

    def test_rx_command_is_not_adapter_tx(self):
        raw = RxFrame()
        raw.head.id_flags = 1
        raw.head.flags_dlc = 0x80
        self.assertEqual(decode_sdk(raw).direction, "RX")

    def test_motor_demultiplex(self):
        one = Frame(10, 20, 0x10, bytes([0x11]) + b"\0" * 7)
        two = Frame(11, 21, 0x10, bytes([0x12]) + b"\0" * 7)
        self.assertNotEqual(one.group, two.group)
        self.assertNotIn("反馈", Frame(12, 22, 0x10, one.data, extended=True).group)

    def test_stuffing_crc_known_vectors(self):
        # CRC15/CAN known check: ASCII 123456789 -> 0x059e.
        bits = [(byte >> bit) & 1 for byte in b"123456789" for bit in range(7, -1, -1)]
        crc = 0
        for bit in bits:
            top = (crc >> 14) ^ bit
            crc = (crc << 1) & 0x7fff
            if top:
                crc ^= 0x4599
        self.assertEqual(crc, 0x059e)
        self.assertEqual(classic_frame_bits(0, b"", dlc=0), 53)
        self.assertTrue(111 <= classic_frame_bits(0x123, b"\x55" * 8) <= 135)
        self.assertTrue(131 <= classic_frame_bits(0x12345, b"\0" * 8, True) <= 160)
        self.assertLess(classic_frame_bits(0x123, b"", remote=True), 70)

    def test_time_windows_expire_and_error_does_not_add_load(self):
        stats = Statistics()
        stats.start_ns = 0
        for i in range(1000):
            stats.add(Frame(i * 1_000_000, 0, 1, b"\0" * 8))
        result = stats.snapshot(1_000_000_000)
        self.assertAlmostEqual(result["streams"][0]["p99_ms"], 1)
        self.assertAlmostEqual(result["streams"][0]["max_ms"], 1)
        self.assertGreater(result["load_percent"][1000], 10)
        stats.add(Frame(2_000_000_000, 0, 0, b"", direction="ERR"))
        result = stats.snapshot(3_000_000_000)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["load_percent"][1000], 0)
        self.assertEqual(result["streams"][0]["fps"], 0)

    def test_capture_high_rate_flush_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.csv"
            capture = Capture(path, metadata={"test": True})
            base = time.monotonic_ns()
            for i in range(20000):
                capture.submit(Frame(base + i * 200000, 100 + i, 0x10,
                                     bytes([0x11 + i % 4]) + b"\0" * 7,
                                     device_timestamp=i))
            result = capture.close()
            with path.open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 20000)
            self.assertEqual(result["queue_dropped"], 0)
            self.assertEqual(rows[-1]["device_timestamp_raw"], "19999")
            self.assertTrue(json.loads(path.with_suffix(".json").read_text())["completed"])

    def test_queue_overflow_is_counted(self):
        capture = Capture(None)
        capture.close()
        capture.accepting = True
        capture.queue = queue.Queue(maxsize=1)
        frame = Frame(1, 2, 1, b"\0" * 8)
        capture.submit(frame)
        capture.submit(frame)
        self.assertEqual(capture.dropped, 1)

    def test_disk_failure_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Capture(Path(folder) / "capture.csv")
            class Broken:
                def writerow(self, row):
                    raise OSError("disk full")
            capture._writer = Broken()
            capture.submit(Frame(time.monotonic_ns(), time.time_ns(), 1, b"\0" * 8))
            result = capture.close()
            self.assertIn("disk full", result["failure"])
            meta = json.loads(capture.path.with_suffix(".json").read_text())
            self.assertFalse(meta["completed"])

    def test_demo_stops_producing_on_close(self):
        frames = []
        backend = DemoBackend()
        backend.start(frames.append)
        time.sleep(.03)
        backend.close()
        count = len(frames)
        time.sleep(.01)
        self.assertGreater(count, 0)
        self.assertEqual(len(frames), count)

    def test_socketcan_receive_and_kernel_drop_counter(self):
        packet = struct.pack("=IB3x8s", 0x10, 8, b"\x11" + b"\0" * 7)
        class FakeSocket:
            def __init__(self):
                self.once = False
            def setsockopt(self, *a): pass
            def settimeout(self, *a): pass
            def bind(self, *a): pass
            def close(self): pass
            def recvmsg(self, *a):
                if not self.once:
                    self.once = True
                    return packet, [(socket.SOL_SOCKET, 40, struct.pack("=I", 3))], 0, None
                time.sleep(.001)
                raise socket.timeout()
        frames = []
        with patch("tools.can_monitor.backends.socket.socket", return_value=FakeSocket()):
            backend = SocketBackend()
            backend.start(frames.append)
            time.sleep(.015)
            backend.close()
        self.assertEqual(len(frames), 1)
        self.assertEqual(backend.kernel_dropped, 3)
        self.assertEqual(frames[0].data[0], 0x11)

    def test_native_configure_receive_and_cleanup_without_can_send(self):
        class FakeSDK:
            def __init__(self):
                self.events = []
            def dmcan_context_create(self, out): out._obj.value = 1
            def dmcan_context_destroy(self, ctx): self.events.append("destroy")
            def dmcan_find_devices(self, ctx): return 1
            def dmcan_device_get(self, ctx, out, index):
                out._obj.value = 2
                return True
            def dmcan_device_open(self, dev): return True
            def dmcan_device_enable_channel(self, dev, ch): return True
            def dmcan_device_disable_channel(self, dev, ch):
                self.events.append("disable")
                return True
            def dmcan_device_close(self, dev): self.events.append("close")
            def dmcan_device_hook_recv_callback(self, dev, callback): self.recv = callback
            def dmcan_device_hook_err_callback(self, dev, callback): self.err = callback
            def dmcan_device_set_channel_baudrate(self, dev, ch, info):
                self.info = info
                return True
            def dmcan_device_get_channel_baudrate(self, dev, ch, out):
                C.memmove(out, C.byref(self.info), C.sizeof(self.info))
                return True
        sdk = FakeSDK()
        frames = []
        with patch("tools.can_monitor.backends.load_sdk", return_value=sdk):
            backend = NativeBackend()
            backend.start(frames.append)
            raw = RxFrame()
            raw.head.id_flags = 0x10
            raw.head.flags_dlc = 0x80
            raw.payload[0] = 0x11
            sdk.recv(None, C.pointer(raw))
            raw.payload[0] = 0x12  # previously queued payload must be copied
            raw.head.channel = 1
            sdk.recv(None, C.pointer(raw))
            backend.close()
            sdk.recv(None, C.pointer(raw))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].data[0], 0x11)
        self.assertEqual(sdk.events, ["disable", "close", "destroy"])
        self.assertEqual(sdk.info.can_baudrate, 1000000)
        self.assertFalse(sdk.info.canfd)

    def test_no_device_cleanup(self):
        class FakeSDK:
            destroyed = False
            def dmcan_context_create(self, out): out._obj.value = 1
            def dmcan_context_destroy(self, ctx): self.destroyed = True
            def dmcan_find_devices(self, ctx): return 0
        sdk = FakeSDK()
        with patch("tools.can_monitor.backends.load_sdk", return_value=sdk):
            backend = NativeBackend()
            with self.assertRaisesRegex(RuntimeError, "0 个"):
                backend.start(lambda f: None)
        self.assertTrue(sdk.destroyed)


if __name__ == "__main__":
    unittest.main()
