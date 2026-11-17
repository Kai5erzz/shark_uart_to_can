from dataclasses import replace
import unittest
from unittest.mock import Mock
from tools.can_monitor.core import Frame
from tools.can_monitor.raw_stream import RawCANStream, ascii_text


class RawStreamTest(unittest.TestCase):
    def test_longtext_reassembly_same_id_echo_and_tx_filter(self):
        stream = RawCANStream()
        stream.configure(1, True)
        send = Mock()
        data = b'ASCII long text\r\n' * 1000
        for start in range(0, len(data), 7):
            frame = Frame(1, 2, 1, data[start:start+7])
            stream.handle(frame, send)
            stream.handle(replace(frame, direction='TX'), send)
        self.assertEqual(stream.snapshot(with_data=True)['data'], data)
        self.assertEqual(b''.join(call.args[1] for call in send.call_args_list), data)
        self.assertTrue(all(call.args[0] == 1 for call in send.call_args_list))
        self.assertEqual(stream.snapshot()['echo_sent'], send.call_count)
        self.assertEqual(ascii_text(data), data.decode('ascii'))

    def test_wrong_frame_types_and_capped_preview(self):
        stream = RawCANStream(limit=10)
        send = Mock()
        frame = Frame(1, 2, 1, b'12345678')
        for changes in (dict(can_id=2), dict(extended=True), dict(remote=True), dict(fd=True),
                        dict(direction='ERR'), dict(data=b'123456789')):
            stream.handle(replace(frame, **changes), send)
        self.assertEqual(stream.snapshot()['bytes'], 0)
        stream.handle(frame, send)
        stream.handle(frame, send)
        self.assertEqual(stream.snapshot(with_data=True)['data'], b'7812345678')
        self.assertEqual(stream.snapshot()['display_trimmed_bytes'], 6)
        send.assert_not_called()
        self.assertEqual(ascii_text(b'\x00\xff\r\n'), '\\x00\\xFF\r\n')
        stream.configure(32, False)
        self.assertEqual(stream.snapshot()['bytes'], 0)

    def test_echo_failure_disable_and_clear(self):
        stream = RawCANStream()
        stream.configure(32, True)
        frame = Frame(1, 2, 32, b'abc')
        stream.handle(frame, Mock(side_effect=OSError('disconnected')))
        self.assertEqual(stream.snapshot()['errors'], 1)
        stream.disable_echo()
        send = Mock()
        stream.handle(frame, send)
        send.assert_not_called()
        self.assertEqual(stream.snapshot()['can_id'], 32)
        stream.clear()
        self.assertEqual(stream.snapshot()['bytes'], 0)


if __name__ == '__main__':
    unittest.main()
