"""Real CLI over PTYs; simulated device, not firmware/hardware validation."""
import os
import pathlib
import pty
import select
import subprocess
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from bridge_protocol import (Decoder, Packet, encode, INFO, INFO_REPLY, ECHO, ECHO_REPLY,
                             CAN_TX, STREAM_TX, CAN_RX, CAN_TEST, ACK, EXT, DEFAULT_ID)


class AssistantTest(unittest.TestCase):
    def run_assistant(self, *args, corrupt=False):
        master, slave = pty.openpty()
        stopped = threading.Event()
        state = dict(can=2, wire=1)
        commands = []

        def device():
            decoder = Decoder()
            def reply(value):
                data = encode(value) if isinstance(value, Packet) else (value + '\r\n').encode()
                os.write(master, data[:2])
                os.write(master, data[2:])
            while not stopped.is_set():
                if not select.select([master], [], [], .05)[0]:
                    continue
                try:
                    data = os.read(master, 4096)
                except OSError:
                    return
                for cmd in decoder.feed(data):
                    commands.append(cmd)
                    if isinstance(cmd, Packet):
                        if cmd.type == INFO:
                            reply(Packet(INFO_REPLY, bytes([0, 7, 7, state['can'], 0, 0, 0, state['wire'], 2]), sequence=cmd.sequence))
                        elif cmd.type == ECHO:
                            reply(Packet(ECHO_REPLY, b'bad' if corrupt else cmd.data, cmd.id, cmd.sequence, cmd.flags))
                        elif cmd.type == CAN_TEST:
                            state['can'] = cmd.data[0]
                            reply(Packet(ACK, bytes([cmd.type, 0]), sequence=cmd.sequence))
                        elif cmd.type in (CAN_TX, STREAM_TX):
                            reply(Packet(ACK, bytes([cmd.type, 0]), sequence=cmd.sequence))
                            chunks = [cmd.data[at:at+8] for at in range(0, len(cmd.data), 8)] or [b'']
                            for chunk in chunks:
                                reply(Packet(CAN_RX, chunk, 7 if cmd.flags & DEFAULT_ID else cmd.id, flags=cmd.flags & EXT))
                    elif cmd == 'ping':
                        reply('pong')
                    elif cmd.startswith('echo '):
                        reply('wrong' if corrupt else cmd[5:])
                    elif cmd.startswith('wire '):
                        state['wire'] = ('ascii', 'binary', 'raw').index(cmd[5:])
                        reply('OK wire')
                    elif cmd.startswith('mode '):
                        state['can'] = ('bridge', 'loopback', 'standby').index(cmd[5:])
                        reply('OK mode=' + cmd[5:])
                    elif cmd.startswith('send'):
                        extended = cmd.startswith('sendx')
                        _, can_id, payload = cmd.split()
                        payload = '' if payload == '-' else payload
                        reply('OK queued')
                        reply(f'CAN {"X" if extended else "S"} {int(can_id, 16):08X} {len(payload)//2} {payload}')
                    elif cmd == 'stats':
                        reply('mode=uart-can fake_device=1')
                    else:
                        reply('ERR unknown')
        worker = threading.Thread(target=device, daemon=True)
        worker.start()
        try:
            result = subprocess.run([sys.executable, str(ROOT / 'tools/serial_assistant.py'),
                                     '--port', os.ttyname(slave), *args], capture_output=True,
                                    text=True, timeout=15)
            return result, state, commands
        finally:
            stopped.set()
            worker.join(timeout=1)
            os.close(master)
            os.close(slave)

    def test_ascii_roundtrip_restores_can_and_wire(self):
        result, state, _ = self.run_assistant('--self-test', '--rounds', '3', '--can-loopback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS 3 echo rounds', result.stdout)
        self.assertIn('PASS internal CAN loopback', result.stdout)
        self.assertEqual(state, dict(can=2, wire=1))

    def test_corrupted_echo_fails(self):
        result, _, _ = self.run_assistant('--self-test', '--rounds', '1', corrupt=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL:', result.stderr)

    def test_binary_loopback_and_restore(self):
        result, state, commands = self.run_assistant('--binary-test', '--rounds', '70', '--can-loopback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS internal CAN loopback', result.stdout)
        self.assertEqual(state, dict(can=2, wire=1))
        self.assertFalse(any(isinstance(c, Packet) and c.type in (0x21, 0x22, 0x23) for c in commands))

    def test_binary_corruption_restores_wire(self):
        result, state, _ = self.run_assistant('--binary-test', '--rounds', '2', corrupt=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(state, dict(can=2, wire=1))

    def test_benchmark(self):
        result, state, _ = self.run_assistant('--benchmark', '--rounds', '20', '--size', '64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('p99=', result.stdout)
        self.assertIn('Echo goodput per direction', result.stdout)
        self.assertEqual(state, dict(can=2, wire=1))

    def test_transparent_full_duplex(self):
        a, aa = pty.openpty()
        b, bb = pty.openpty()
        stopped = threading.Event()
        def bridge():
            while not stopped.is_set():
                ready, _, _ = select.select([a, b], [], [], .05)
                for source in ready:
                    try:
                        data = os.read(source, 4096)
                        os.write(b if source == a else a, data)
                    except OSError:
                        return
        thread = threading.Thread(target=bridge, daemon=True)
        thread.start()
        try:
            result = subprocess.run([sys.executable, str(ROOT / 'tools/serial_assistant.py'),
                '--port', os.ttyname(aa), '--peer', os.ttyname(bb), '--bridge-test',
                '--rounds', '50', '--size', '256'], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS full duplex', result.stdout)
        finally:
            stopped.set(); thread.join(timeout=1)
            for fd in (a, aa, b, bb):
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
