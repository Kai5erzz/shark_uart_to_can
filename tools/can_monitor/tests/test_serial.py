import time
import unittest
from unittest.mock import patch

from tools.can_monitor.app import discover, discovery_text
from tools.can_monitor.serial_backend import SerialBackend, SerialParser, decode_serial

# Wire packets from the documented packed layout, including header-like payload.
RX = bytes.fromhex('AA 11 08 10 00 00 00 11 AA 55 00 04 05 06 07 55')
ACK = bytes.fromhex('AA 13 00 00 00 00 00 00 00 00 00 00 00 00 00 55')


class SerialTests(unittest.TestCase):
    def test_every_fragment_boundary_and_batch(self):
        for boundary in range(len(RX) + 1):
            parser = SerialParser()
            packets = parser.feed(RX[:boundary]) + parser.feed(RX[boundary:] + ACK + RX)
            self.assertEqual(packets, [RX, ACK, RX])
            self.assertEqual(parser.discarded, 0)
        frame = decode_serial(RX, 1, 2)
        self.assertEqual((frame.can_id, frame.data, frame.direction),
                         (0x10, bytes.fromhex('11 AA 55 00 04 05 06 07'), 'RX'))
        self.assertIsNone(frame.device_timestamp)

    def test_noise_invalid_id_dlc_and_resynchronization(self):
        bad = bytes.fromhex('AA 11 09 FF FF FF FF 00 00 00 00 00 00 00 00 55')
        parser = SerialParser()
        self.assertEqual(parser.feed(b'garbage' + bad + RX + b'\xaa'), [RX])
        self.assertEqual(parser.discarded, 7 + 16)
        self.assertEqual(parser.buffer, b'\xaa')
        self.assertGreater(parser.malformed, 0)

    def test_remote_and_extended(self):
        raw = bytes.fromhex('AA 11 C8 FF FF FF 1F 00 00 00 00 00 00 00 00 55')
        self.assertEqual(SerialParser().feed(raw), [raw])
        frame = decode_serial(raw)
        self.assertTrue(frame.remote and frame.extended)
        self.assertEqual(frame.data, b'')
        self.assertEqual(frame.dlc, 8)

    def test_actual_device_short_payload_and_initial_partial_packet(self):
        raw = bytes.fromhex('AA 11 05 23 01 00 00 00 0D 0A A5 FF 03 04 05 55')
        parser = SerialParser()
        self.assertEqual(parser.feed(b'\x05\x55' + raw[:9]), [])
        packets = parser.feed(raw[9:])
        self.assertEqual(packets, [raw])
        frame = decode_serial(packets[0])
        self.assertEqual((frame.can_id, frame.dlc, frame.data), (0x123, 5, bytes.fromhex('00 0D 0A A5 FF')))

    def test_native_failure_does_not_hide_cdc_device(self):
        port = {"port": "/dev/ttyACM0", "vid": 0x2e88, "pid": 0x4603, "candidate": True}
        with patch('tools.can_monitor.app.scan_native', side_effect=RuntimeError('missing SDK')), \
             patch('tools.can_monitor.app.scan_serial', return_value=[port]):
            found = discover()
        self.assertEqual(found['ports'], [port])
        self.assertIn('2E88:4603', discovery_text(found))
        self.assertIn('missing SDK', discovery_text(found))

    def test_config_ack_receive_and_no_can_transmit(self):
        class Port:
            in_waiting = 0
            def __init__(self):
                self.writes = []
                self.pending = b''
                self.closed = False
            def reset_input_buffer(self): pass
            def flush(self): pass
            def write(self, data):
                self.writes.append(data)
                if data == bytes.fromhex('55 05 00 AA 55'):
                    self.pending = ACK + RX
                return len(data)
            def read(self, size):
                time.sleep(.005)
                data, self.pending = self.pending, b''
                return data
            def close(self): self.closed = True
        port, frames = Port(), []
        with patch('serial.Serial', return_value=port):
            backend = SerialBackend('/dev/ttyACM0')
            backend.start(frames.append)
            backend.close()
        self.assertEqual(port.writes, [bytes.fromhex(x) for x in (
            '55 03 AA 55', '55 05 00 AA 55')])
        self.assertTrue(port.closed)
        self.assertEqual(len(frames), 1)  # Configuration status never enters CAN statistics.
        self.assertTrue(backend.metadata['configuration_acknowledged'])
        self.assertEqual(backend.metadata['adapter_status_counts'], {'0x13': 1, '0x11': 1})

    def test_no_ack_keeps_listening_then_receives_late_can(self):
        class SilentPort:
            in_waiting = 0
            closed = False
            pending = b''
            def reset_input_buffer(self): pass
            def flush(self): pass
            def write(self, data): return len(data)
            def read(self, size):
                time.sleep(.01)
                data, self.pending = self.pending, b''
                return data
            def close(self): self.closed = True
        port = SilentPort()
        frames = []
        with patch('serial.Serial', return_value=port):
            backend = SerialBackend('/dev/ttyACM0')
            backend.start(frames.append)
            self.assertFalse(port.closed)
            self.assertIn('等待 CAN 数据', backend.status_text())
            self.assertFalse(backend.metadata['protocol_verified'])
            self.assertNotIn('configured_bitrate', backend.metadata)
            port.pending = RX
            end = time.monotonic() + 1
            while not frames and time.monotonic() < end:
                time.sleep(.01)
            backend.close()
        self.assertTrue(port.closed)
        self.assertEqual(len(frames), 1)
        self.assertTrue(backend.metadata['protocol_verified'])
        self.assertEqual(backend.metadata['can_rx_count'], 1)
        self.assertFalse(backend.metadata['configuration_acknowledged'])
        self.assertIn('设备未返回配置应答', backend.status_text())

    def test_unplug_is_reported_and_worker_stops(self):
        class UnpluggedPort:
            in_waiting = 0
            unplugged = False
            closed = False
            def reset_input_buffer(self): pass
            def flush(self): pass
            def write(self, data): return len(data)
            def read(self, size):
                time.sleep(.005)
                if self.unplugged: raise OSError('device disconnected')
                return b''
            def close(self): self.closed = True
        port = UnpluggedPort()
        with patch('serial.Serial', return_value=port):
            backend = SerialBackend('/dev/ttyACM0')
            backend.start(lambda f: None)
            port.unplugged = True
            backend.thread.join(timeout=1)
            self.assertIn('device disconnected', backend.failure)
            backend.close()
        self.assertTrue(port.closed)

    def test_rejected_bitrate_is_still_a_failure(self):
        backend = SerialBackend('/dev/ttyACM0')
        with self.assertRaisesRegex(RuntimeError, '拒绝'):
            backend._packets(bytes.fromhex('AA 03 00 00 00 00 00 00 00 00 00 00 00 00 00 55'),
                             lambda f: None)

    def test_adapter_transmit_failures_visible_before_disconnect(self):
        backend = SerialBackend('/dev/ttyACM0')
        failed = bytes.fromhex('AA 02 08 05 00 00 00 01 02 03 04 05 06 07 08 55')
        frames = []
        backend._packets(failed + RX, frames.append)
        self.assertEqual(backend.metadata['adapter_status_counts'], {'0x02': 1, '0x11': 1})
        self.assertIn('适配器发送失败 1', backend.status_text())
        self.assertEqual(len(frames), 1)  # Status is not an RX frame or an echo trigger.


if __name__ == '__main__':
    unittest.main()
