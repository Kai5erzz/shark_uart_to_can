"""Product diagnostics used by serial_assistant; commands never save Flash."""
import concurrent.futures
import secrets
import time

import serial
from bridge_protocol import (ACK, CAN_RX, CAN_TEST, CAN_TX, DEFAULT_ID, ECHO,
                             ECHO_REPLY, EXT, REQUEST_ACK, STREAM_TX,
                             Client, Packet, WIRES)

BAUD = 921600


def binary_test(port_name, rounds, can_loopback=False):
    with serial.Serial(port_name, BAUD, timeout=0.02, write_timeout=2) as port:
        client = Client(port)
        info = client.info()
        print('INFO', info)
        original_wire = WIRES[info['wires'][info['port']]]
        try:
            client.command('wire binary')
            for i in range(rounds):
                payload = bytes((j + i) % 256 for j in range(i % 65))
                reply = client.request(ECHO, payload)
                if reply.data != payload:
                    raise OSError('Binary echo content mismatch')
            print(f'PASS {rounds} binary echo rounds (0..64 bytes, zero/CR/LF supported)')
            if can_loopback:
                client.request(CAN_TEST, b'\x01')
                for can_id, payload, flags in ((0x123, bytes(range(8)), 0),
                                               (0x1abcdeff, b'\x00\r\n\xa5', EXT),
                                               (0, b'', 0)):
                    client.request(CAN_TX, payload, can_id, flags | REQUEST_ACK)
                    client.wait(lambda p: isinstance(p, Packet) and p.type == CAN_RX
                                and p.id == can_id and p.data == payload and p.flags == flags)
                payload = bytes(range(64))
                client.request(STREAM_TX, payload, flags=REQUEST_ACK | DEFAULT_ID)
                for at in range(0, 64, 8):
                    client.wait(lambda p: isinstance(p, Packet) and p.type == CAN_RX
                                and p.id == info['id'] and p.data == payload[at:at + 8])
                print('PASS internal CAN loopback: standard/extended/empty + 64-byte stream')
        finally:
            try:
                if can_loopback:
                    client.request(CAN_TEST, bytes([info['can_state']]))
            finally:
                client.command('wire ' + original_wire)


def benchmark(port_name, rounds, size):
    """Sequential echo RTT, then bounded-window echo application goodput."""
    with serial.Serial(port_name, BAUD, timeout=0.01, write_timeout=2) as port:
        client = Client(port)
        print('INFO', client.info())
        timings = []
        payload = secrets.token_bytes(size)
        for _ in range(10):
            if client.request(ECHO, payload).data != payload:
                raise OSError('Warmup echo mismatch')
        for _ in range(rounds):
            started = time.perf_counter_ns()
            reply = client.request(ECHO, payload)
            timings.append((time.perf_counter_ns() - started) / 1e6)
            if reply.data != payload:
                raise OSError('Echo mismatch')
        ordered = sorted(timings)
        def percentile(p):
            return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
        print(f'RTT ms n={rounds} payload={size}: p50={percentile(.5):.3f} '
              f'p95={percentile(.95):.3f} p99={percentile(.99):.3f} max={max(ordered):.3f}')
        started = time.perf_counter()
        pending = {}
        submitted = completed = 0
        while completed < rounds:
            while submitted < rounds and len(pending) < 8:
                data = secrets.token_bytes(size)
                seq = client.send(ECHO, data)
                pending[seq] = data
                submitted += 1
            reply = client.wait(lambda p: isinstance(p, Packet) and p.type == ECHO_REPLY and p.sequence in pending)
            if reply.data != pending.pop(reply.sequence):
                raise OSError('Pipelined echo mismatch')
            completed += 1
        elapsed = time.perf_counter() - started
        print(f'Echo goodput per direction: {rounds * size / elapsed:.0f} payload B/s '
              f'(window=8, {elapsed:.3f}s); includes host/USB/driver scheduling, not CAN wire latency')


def transparent_test(usb_name, uart_name, rounds, size):
    """Board must already be in usb-ttl; sends arbitrary bytes in both directions."""
    if usb_name == uart_name:
        raise ValueError('USB and UART peer must be different ports')
    with serial.Serial(usb_name, BAUD, timeout=.02, write_timeout=3) as usb, \
         serial.Serial(uart_name, BAUD, timeout=.02, write_timeout=3) as uart:
        usb.reset_input_buffer()
        uart.reset_input_buffer()
        def direction(source, destination):
            count = 0
            started = time.perf_counter()
            for _ in range(rounds):
                payload = secrets.token_bytes(size)
                if source.write(payload) != len(payload):
                    raise OSError('Incomplete transparent write')
                received = bytearray()
                deadline = time.monotonic() + 3
                while len(received) < size and time.monotonic() < deadline:
                    received.extend(destination.read(size - len(received)))
                if bytes(received) != payload:
                    raise OSError('Transparent mismatch/timeout; select usb-ttl and connect peer to USART1')
                count += size
            return count, count / (time.perf_counter() - started)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            forward = pool.submit(direction, usb, uart)
            reverse = pool.submit(direction, uart, usb)
            a, b = forward.result(), reverse.result()
        print(f'PASS full duplex USB <-> USART1: {a[0]} / {b[0]} bytes; '
              f'USB->UART {a[1]:.0f} B/s, UART->USB {b[1]:.0f} B/s')
