import ctypes as ct
import pathlib
import random
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from bridge_protocol import Packet, Decoder, encode, decode, cobs_encode


class CPacket(ct.Structure):
    _fields_ = [('id', ct.c_uint32), ('sequence', ct.c_uint16), ('type', ct.c_uint8),
                ('flags', ct.c_uint8), ('length', ct.c_uint8), ('data', ct.c_uint8 * 64)]


class BinaryProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        lib = pathlib.Path(cls.temp.name) / 'protocol.so'
        subprocess.run(['gcc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
            '-I' + str(ROOT / 'uart_to_can/App/Inc'), str(ROOT / 'uart_to_can/App/Src/bridge_protocol.c'),
            '-o', str(lib)], check=True)
        cls.c = ct.CDLL(str(lib))
        cls.c.Bridge_Encode.argtypes = [ct.POINTER(CPacket), ct.c_void_p, ct.c_size_t]
        cls.c.Bridge_Encode.restype = ct.c_size_t
        cls.c.Bridge_Decode.argtypes = [ct.c_void_p, ct.c_size_t, ct.POINTER(CPacket)]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cross_language_all_bytes_and_lengths(self):
        rng = random.Random(914)
        for i in range(1024):
            p = Packet(rng.randrange(256), bytes((j + i) % 256 for j in range(i % 65)),
                       rng.randrange(2**32), rng.randrange(65536), rng.randrange(256))
            c = CPacket(p.id, p.sequence, p.type, p.flags, len(p.data))
            c.data[:len(p.data)] = p.data
            out = ct.create_string_buffer(80)
            n = self.c.Bridge_Encode(ct.byref(c), out, 80)
            self.assertEqual(out.raw[:n], encode(p))
            self.assertEqual(decode(out.raw[2:n-1]), p)
            got = CPacket()
            body = encode(p)[2:-1]
            self.assertEqual(self.c.Bridge_Decode(body, len(body), ct.byref(got)), 1)
            self.assertEqual((got.id, got.sequence, got.type, got.flags, bytes(got.data[:got.length])),
                             (p.id, p.sequence, p.type, p.flags, p.data))

    def test_golden_vector_and_fragmentation(self):
        p = Packet(0x10, bytes([0, 13, 10, 0xa5, 255]), 0x123, 0x1234, 2)
        wire = encode(p)
        # Independent fixed on-wire fixture, CRC-16 CCITT-FALSE, little endian.
        self.assertEqual(wire.hex(), '00a50901100205341223010101070d0aa5ffe48900')
        stream = b'ping\r\n' + wire * 2 + b'echo ok\n'
        for chunk in range(1, len(stream)):
            decoder = Decoder()
            got = []
            for at in range(0, len(stream), chunk):
                got.extend(decoder.feed(stream[at:at+chunk]))
            self.assertEqual(got, ['ping', p, p, 'echo ok'])
            self.assertEqual(decoder.errors, 0)

    def test_corruption_and_recovery(self):
        p = Packet(1, b'\x00\r\n\xff', 0x321, 5)
        wire = encode(p)
        bad = bytearray(wire)
        bad[-2] ^= 1
        decoder = Decoder()
        events = decoder.feed(bytes(bad) + b'X' * 200 + b'\n' + b'\x00\xa5' + b'\x01' * 90 + b'\x00' + wire)
        self.assertEqual(events, [p])
        self.assertEqual(decoder.errors, 3)
        for body in (b'', b'\x00', b'\xff\x01', b'\x02\x00', cobs_encode(b'\x02' * 12)):
            with self.assertRaises(ValueError):
                decode(body)
        with self.assertRaises(ValueError):
            encode(Packet(1, b'x' * 65))


if __name__ == '__main__':
    unittest.main()
