#!/usr/bin/env python3
"""SHARK serial console: GUI, terminal and repeatable CH340/CDC self-test.

Dependency: pyserial>=3.5; GUI additionally needs tkinter.
"""
from __future__ import annotations

import argparse
import queue
import secrets
import sys
import threading
import time

import serial
from serial.tools import list_ports
from bridge_protocol import (CAN_TX, STREAM_TX, ECHO, EXT, DEFAULT_ID, REQUEST_ACK,
                             Packet, Decoder, Client, WIRES, encode)
from bridge_diagnostics import binary_test, benchmark, transparent_test
from periodic_sender import PeriodicSender
from can_performance import Config as PerformanceConfig, run as run_performance, export as export_performance
from performance_ui import PerformancePanel
from raw_serial_test import run as run_raw_test
from raw_test_ui import RawTestPanel


BAUD = 921600


def send_command(port, command: str) -> None:
    data = (command.rstrip("\r\n") + "\r\n").encode("ascii")
    if port.write(data) != len(data):
        raise serial.SerialTimeoutException("Incomplete serial write")
    port.flush()


def await_line(port, expected: str, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        line = port.readline().decode("ascii", errors="replace").strip()
        if line:
            seen.append(line)
            if line == expected:
                return line
    raise TimeoutError(f"Expected {expected!r}, received {seen[-10:]!r}")


def self_test(port_name: str, rounds: int, can_loopback: bool) -> None:
    with serial.Serial(port_name, BAUD, timeout=0.1, write_timeout=2) as port:
        time.sleep(0.4)
        port.reset_input_buffer()
        send_command(port, "ping")
        await_line(port, "pong")
        print("PASS ping / pong")
        total = 0
        for i in range(rounds):
            # Stays below the firmware line limit; verifies ordering and content.
            payload = f"CH340_{i:04d}_" + secrets.token_hex(64)
            send_command(port, "echo " + payload)
            await_line(port, payload)
            total += len(payload)
        print(f"PASS {rounds} echo rounds, {total} payload bytes each direction at {BAUD} 8N1")
        if can_loopback:
            client = Client(port)
            original = client.info()
            original_wire = WIRES[original['wires'][original['port']]]
            try:
                client.command('wire ascii')
                send_command(port, "mode loopback")
                await_line(port, "OK mode=loopback")
                for command, expected in (
                    ("send 123 55AA000102030405", "CAN S 00000123 8 55AA000102030405"),
                    ("sendx 1ABCDEFF 10203040", "CAN X 1ABCDEFF 4 10203040"),
                    ("send 0 -", "CAN S 00000000 0"),
                ):
                    send_command(port, command)
                    await_line(port, expected)
                print("PASS internal CAN loopback: standard, extended and empty data frame")
            finally:
                previous = ('bridge', 'loopback', 'standby')[original['can_state']]
                try:
                    send_command(port, "mode " + previous)
                    await_line(port, "OK mode=" + previous)
                finally:
                    client.command('wire ' + original_wire)
        send_command(port, "stats")
        until = time.monotonic() + 0.5
        while time.monotonic() < until:
            line = port.readline().decode(errors="replace").strip()
            if line:
                print(line)


def terminal(port_name: str) -> None:
    stopped = threading.Event()
    with serial.Serial(port_name, BAUD, timeout=0.1, write_timeout=2) as port:
        def read():
            try:
                while not stopped.is_set():
                    data = port.read(port.in_waiting or 1)
                    if data:
                        print(data.decode(errors="replace"), end="", flush=True)
            except serial.SerialException as exc:
                print(f"\nDisconnected: {exc}", file=sys.stderr)
                stopped.set()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        print(f"Connected {port_name}, {BAUD} 8N1. Enter help; Ctrl+C exits.")
        try:
            while not stopped.is_set():
                send_command(port, input())
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            stopped.set()
            reader.join(timeout=1)


def gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("SHARK 串口调试助手 · CH340 / USB CDC")
    root.geometry("1180x820")
    events = queue.Queue(maxsize=256)
    connection = None
    worker = None
    stop = threading.Event()
    received = sent = 0
    display_dropped = 0
    rx_decoder = Decoder(line_limit=1023)
    sequence = 0
    periodic = None
    performance_thread = None
    performance_cancel = threading.Event()
    performance_events = queue.SimpleQueue()
    disconnect_pending = close_pending = False
    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    device = tk.StringVar()
    selector = ttk.Combobox(top, textvariable=device, width=27)
    selector.pack(side="left")

    def refresh():
        values = [p.device for p in list_ports.comports()]
        selector["values"] = values
        if values and not device.get():
            device.set(values[0])

    ttk.Button(top, text="刷新", command=refresh).pack(side="left", padx=5)
    baud = tk.StringVar(value=str(BAUD))
    ttk.Combobox(top, textvariable=baud, values=[921600, 115200, 460800], width=10).pack(side="left")
    ttk.Label(top, text="8N1 · 无流控").pack(side="left", padx=8)
    status = tk.StringVar(value="未连接")
    pages = ttk.Notebook(root)
    pages.pack(fill='both', expand=True, padx=8, pady=4)
    console = ttk.Frame(pages)
    pages.add(console, text='串口调试 / 定时发送')
    transcript = ScrolledText(console, wrap="word", font=("Monospace", 10))
    transcript.pack(fill="both", expand=True, padx=8, pady=4)
    transcript.tag_config("tx", foreground="#0868ac")
    transcript.tag_config("error", foreground="#be2431")

    def log(text, tag=None):
        transcript.insert("end", text, tag)
        # Bound memory when monitoring a continuous CAN stream.
        if int(transcript.index("end-1c").split(".")[0]) > 5000:
            transcript.delete("1.0", "1000.0")
        transcript.see("end")

    def disconnect():
        nonlocal connection, worker, disconnect_pending
        if periodic:
            periodic.stop()
        if performance_thread:
            performance_cancel.set()
            disconnect_pending = True
            status.set('正在停止测试并恢复设备配置…')
            return
        stop.set()
        if worker:
            worker.join(timeout=0.3)
            worker = None
        if connection:
            connection.close()
            connection = None
        connect_button.configure(text="连接")
        status.set("未连接")

    def connect():
        nonlocal connection, worker, rx_decoder
        if connection:
            disconnect()
            return
        try:
            connection = serial.Serial(device.get(), int(baud.get()), timeout=0.05, write_timeout=0.2)
        except (ValueError, serial.SerialException) as exc:
            messagebox.showerror("连接失败", str(exc))
            return
        stop.clear()
        rx_decoder = Decoder(line_limit=1023)
        start_reader()
        connect_button.configure(text="断开")
        log(f"\n已连接 {device.get()} @ {baud.get()}\n")

    def start_reader():
        nonlocal worker
        current = connection
        stop.clear()

        def reader():
            nonlocal received, display_dropped
            try:
                while not stop.is_set():
                    data = current.read(current.in_waiting or 1)
                    if data:
                        received += len(data)
                        try:
                            events.put_nowait(("rx", data))
                        except queue.Full:
                            display_dropped += len(data)
            except serial.SerialException as exc:
                try:
                    events.put_nowait(("error", str(exc)))
                except queue.Full:
                    stop.set()

        worker = threading.Thread(target=reader, daemon=True)
        worker.start()

    def prepare_test():
        nonlocal worker, rx_decoder
        if performance_thread:
            raise ValueError('已有测试正在运行，请先停止当前测试')
        if not connection:
            raise ValueError('请先连接测试串口')
        periodic.stop()
        stop.set()
        if worker:
            worker.join(timeout=.4)
            if worker.is_alive():
                stop.clear()
                raise ValueError('接收线程尚未停止，请重试')
            worker = None
        rx_decoder = Decoder(line_limit=1023)
        performance_cancel.clear()
        return connection

    def start_performance(config):
        nonlocal performance_thread
        if raw_stream.get():
            raise ValueError('协议性能测试使用USB/USART1；USART2请使用RAW双向测试页')
        current = prepare_test()
        panel.set_running(True)
        def test():
            try:
                result = run_performance(current, config, performance_cancel,
                    lambda event: performance_events.put(('progress', event)))
                performance_events.put(('done', result))
            except Exception as exc:
                performance_events.put(('failure', str(exc)))
        performance_thread = threading.Thread(target=test, name='shark-performance', daemon=True)
        performance_thread.start()

    def start_raw(config):
        nonlocal performance_thread
        current = prepare_test()
        raw_stream.set(True)
        parse_binary.set(False)
        raw_panel.set_running(True)
        def test():
            try:
                result = run_raw_test(current, config, performance_cancel,
                    lambda event: performance_events.put(('raw_progress', event)))
                performance_events.put(('raw_done', result))
            except Exception as exc:
                performance_events.put(('raw_failure', str(exc)))
        performance_thread = threading.Thread(target=test, name='shark-raw-test', daemon=True)
        performance_thread.start()

    connect_button = ttk.Button(top, text="连接", command=connect)
    connect_button.pack(side="left", padx=6)
    display_hex = tk.BooleanVar()
    ttk.Checkbutton(top, text="HEX显示", variable=display_hex).pack(side="left")
    parse_binary = tk.BooleanVar(value=True)
    ttk.Checkbutton(top, text="协议解码", variable=parse_binary).pack(side="left")
    ttk.Button(top, text="清屏", command=lambda: transcript.delete("1.0", "end")).pack(side="right")
    text = tk.StringVar(value="ping")
    bottom = ttk.Frame(console, padding=8)
    bottom.pack(fill="x")
    entry = ttk.Entry(bottom, textvariable=text)
    entry.pack(side="left", fill="x", expand=True)
    hex_send = tk.BooleanVar()
    ttk.Checkbutton(bottom, text="HEX发送", variable=hex_send).pack(side="left", padx=5)
    newline = tk.BooleanVar(value=True)
    ttk.Checkbutton(bottom, text="CRLF", variable=newline).pack(side="left")
    raw_stream = tk.BooleanVar()
    ttk.Checkbutton(bottom, text="透明/原始流", variable=raw_stream,
                    command=lambda: newline.set(not raw_stream.get())).pack(side="left")

    def write_data(data):
        nonlocal sent
        if performance_thread:
            messagebox.showinfo('性能测试进行中', '请先停止性能测试，再发送其他数据。')
            return False
        if not connection:
            messagebox.showinfo("尚未连接", "请先选择并连接 CH340 或 USB CDC 串口。")
            return False
        try:
            count = connection.write(data)
            sent += count
            if count != len(data):
                raise serial.SerialTimeoutException("Incomplete write")
            log("→ " + data.decode(errors="replace") + ("\n" if not data.endswith(b"\n") else ""), "tx")
            return True
        except serial.SerialException as exc:
            log(f"发送失败: {exc}\n", "error")
            disconnect()
            return False

    def text_data():
        data = bytes.fromhex(text.get()) if hex_send.get() else text.get().encode("utf-8")
        data += b"\r\n" if newline.get() and not raw_stream.get() else b""
        if not data:
            raise ValueError('发送内容不能为空')
        return data

    def send(_=None):
        try:
            write_data(text_data())
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))

    entry.bind("<Return>", send)
    ttk.Button(bottom, text="发送", command=send).pack(side="left", padx=5)
    quick = ttk.Frame(console, padding=(8, 0, 8, 8))
    quick.pack(fill="x")
    def command(cmd):
        if raw_stream.get():
            messagebox.showinfo("原始数据流", "请通过按键切回CAN模式，再用USB/USART1配置；USART2为专用RAW入口。")
            return
        periodic.stop()
        write_data((cmd + '\r\n').encode('ascii'))

    for label, cmd in [("Ping", "ping"), ("帮助", "help"), ("统计", "stats"),
                       ("正常CAN", "can normal"), ("内部回环", "can loopback"),
                       ("待机", "can standby"), ("测试帧", "test"), ("LED全亮", "leds F"),
                       ("LED自动", "leds auto")]:
        ttk.Button(quick, text=label, command=lambda c=cmd: command(c)).pack(side="left", padx=2)
    config = ttk.Frame(console, padding=(8, 0, 8, 8))
    config.pack(fill='x')
    for label, cmd in [('UART→CAN', 'mode uart-can'), ('USB→CAN', 'mode usb-can'),
                       ('USB↔USART1', 'mode usb-ttl')]:
        ttk.Button(config, text=label, command=lambda c=cmd: command(c)).pack(side='left', padx=2)
    ttk.Label(config, text='ID(十进制)').pack(side='left', padx=5)
    chosen_id = tk.StringVar(value='1')
    ttk.Spinbox(config, from_=1, to=32, textvariable=chosen_id, width=4).pack(side='left')
    ttk.Button(config, text='设置ID', command=lambda: command('id ' + chosen_id.get())).pack(side='left')
    ttk.Button(config, text='保存ID', command=lambda: command('save')).pack(side='left', padx=2)
    for value in ('ascii', 'binary', 'raw'):
        ttk.Button(config, text='wire ' + value, command=lambda v=value: command('wire ' + v)).pack(side='left', padx=2)
    binary = ttk.Frame(console, padding=(8, 0, 8, 8))
    binary.pack(fill='x')
    ttk.Label(binary, text='二进制 CAN ID(HEX)').pack(side='left')
    can_id = tk.StringVar(value='123')
    ttk.Entry(binary, textvariable=can_id, width=10).pack(side='left', padx=3)
    payload = tk.StringVar(value='00 0D 0A A5 FF')
    ttk.Entry(binary, textvariable=payload, width=48).pack(side='left', padx=3)
    extended, default_id = tk.BooleanVar(), tk.BooleanVar()
    ttk.Checkbutton(binary, text='扩展ID', variable=extended).pack(side='left')
    ttk.Checkbutton(binary, text='使用板上ID', variable=default_id).pack(side='left')

    def binary_message(echo=False):
        if raw_stream.get():
            raise ValueError('原始流不能发送二进制协议帧，请在协议端口执行此操作')
        data = bytes.fromhex(payload.get())
        identifier = int(can_id.get(), 16)
        if len(data) > 64 or not 0 <= identifier <= (0x1fffffff if extended.get() else 0x7ff):
            raise ValueError('数据最多64字节，ID必须在CAN范围内')
        kind = ECHO if echo else STREAM_TX if len(data) > 8 else CAN_TX
        flags = REQUEST_ACK | (EXT if extended.get() else 0) | (DEFAULT_ID if default_id.get() else 0)
        return kind, data, identifier, flags

    def write_binary(message):
        nonlocal sequence
        kind, data, identifier, flags = message
        sequence = (sequence + 1) & 0xffff
        return write_data(encode(Packet(kind, data, identifier, sequence, flags)))

    def binary_send(echo=False):
        try:
            write_binary(binary_message(echo))
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('输入错误', str(exc))
    ttk.Button(binary, text='发送CAN/分包', command=binary_send).pack(side='left')
    ttk.Button(binary, text='二进制回显', command=lambda: binary_send(True)).pack(side='left', padx=2)
    timer = ttk.Frame(console, padding=(8, 0, 8, 8))
    timer.pack(fill='x')
    ttk.Label(timer, text='定时发送').pack(side='left')
    timer_source = tk.StringVar(value='文本 / HEX')
    source_selector = ttk.Combobox(timer, textvariable=timer_source, state='readonly',
                                   values=('文本 / HEX', '二进制 CAN', '二进制回显'), width=14)
    source_selector.pack(side='left', padx=5)
    ttk.Label(timer, text='间隔(ms)').pack(side='left')
    timer_interval = tk.StringVar(value='100')
    interval_entry = ttk.Entry(timer, textvariable=timer_interval, width=8)
    interval_entry.pack(side='left', padx=5)
    ttk.Label(timer, text='次数(0=持续)').pack(side='left')
    timer_limit = tk.StringVar(value='0')
    limit_entry = ttk.Entry(timer, textvariable=timer_limit, width=8)
    limit_entry.pack(side='left', padx=5)
    timer_status = tk.StringVar(value='已停止 · 已发 0 次')

    def timer_changed():
        active = periodic.running
        timer_button.configure(text='停止定时' if active else '开始定时')
        source_selector.configure(state='disabled' if active else 'readonly')
        interval_entry.configure(state='disabled' if active else 'normal')
        limit_entry.configure(state='disabled' if active else 'normal')
        timer_status.set(f'{"发送中" if active else "已停止"} · 已发 {periodic.count} 次')

    def toggle_timer():
        if periodic.running:
            periodic.stop()
            return
        if performance_thread:
            messagebox.showinfo('性能测试进行中', '请先停止性能测试，再开始定时发送。')
            return
        if not connection:
            messagebox.showinfo('尚未连接', '请先连接串口，再开始定时发送。')
            return
        try:
            try:
                interval = int(timer_interval.get())
                limit = int(timer_limit.get())
            except ValueError:
                raise ValueError('间隔和次数必须填写整数') from None
            # Freeze this run's payload/options. Binary sequence is regenerated per send.
            if timer_source.get() == '文本 / HEX':
                data = text_data()
                transmit = lambda: write_data(data)
            else:
                message = binary_message(timer_source.get() == '二进制回显')
                transmit = lambda: write_binary(message)
            periodic.start(transmit, interval, limit)
        except ValueError as exc:
            messagebox.showerror('定时发送设置错误', str(exc))

    timer_button = ttk.Button(timer, text='开始定时', command=toggle_timer)
    timer_button.pack(side='left', padx=5)
    ttk.Label(timer, textvariable=timer_status).pack(side='left', padx=5)
    ttk.Label(timer, text='启动时锁定内容').pack(side='left', padx=5)
    periodic = PeriodicSender(root.after, root.after_cancel, timer_changed)
    raw_stream.trace_add('write', lambda *_: periodic.stop())
    ttk.Label(console, text='USART2固定RAW双向CAN；USB↔USART1透明模式不解析命令，需按键切回CAN模式后配置。', padding=4).pack(fill='x')
    panel = PerformancePanel(pages, start_performance, performance_cancel.set)
    pages.add(panel, text='CAN 延迟 / 吞吐测试')
    raw_panel = RawTestPanel(pages, start_raw, performance_cancel.set)
    pages.add(raw_panel, text='USART2 RAW / 双向长文本')
    def select_page(_):
        if pages.select() == str(raw_panel) and not performance_thread:
            raw_stream.set(True)
            parse_binary.set(False)
    pages.bind('<<NotebookTabChanged>>', select_page)
    ttk.Label(root, textvariable=status, padding=5).pack(fill="x")

    def tick():
        nonlocal received, performance_thread, disconnect_pending
        while not performance_events.empty():
            kind, value = performance_events.get_nowait()
            if kind == 'progress':
                panel.update_progress(value)
            elif kind == 'raw_progress':
                raw_panel.update_progress(value)
            else:
                performance_thread = None
                target = raw_panel if kind.startswith('raw_') else panel
                if kind in ('done', 'raw_done'):
                    target.finish(value)
                else:
                    target.set_running(False)
                    target.status.set('测试失败：' + value)
                if disconnect_pending:
                    disconnect_pending = False
                    disconnect()
                elif connection:
                    start_reader()
                if close_pending:
                    root.destroy()
                    return
        # Keep UI responsive even when the device continuously streams CAN data.
        for _ in range(100):
            try:
                kind, data = events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                log(f"\n连接断开: {data}\n", "error")
                disconnect()
            else:
                if pages.select() == str(raw_panel):
                    raw_panel.receive_passive(data)
                if display_hex.get():
                    log(data.hex(' ') + ' ')
                elif parse_binary.get() and not raw_stream.get():
                    for event in rx_decoder.feed(data):
                        if isinstance(event, Packet):
                            log(f'BIN type={event.type:02X} seq={event.sequence} flags={event.flags:02X} '
                                f'id={event.id:X} data={event.data.hex(" ")}\n')
                        else:
                            log(event + '\n')
                else:
                    log(data.decode(errors='replace'))
        if connection:
            status.set(f"{connection.port} @ {connection.baudrate} · RX {received} 字节 · TX {sent} 字节 · 显示丢弃 {display_dropped} 字节")
        root.after(40, tick)

    def close():
        nonlocal close_pending
        if performance_thread:
            close_pending = True
            disconnect()
            return
        disconnect()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    refresh()
    tick()
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List serial ports")
    parser.add_argument("--port", help="/dev/ttyUSB0, /dev/ttyACM0 or COMx")
    parser.add_argument("--terminal", action="store_true", help="Use text console")
    parser.add_argument("--self-test", action="store_true", help="Verify ping and random echo")
    parser.add_argument("--can-loopback", action="store_true", help="Also temporarily select CAN internal loopback")
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument('--binary-test', action='store_true', help='Binary echo; optional CAN loopback')
    parser.add_argument('--benchmark', action='store_true', help='Binary RTT and pipelined echo goodput')
    parser.add_argument('--bridge-test', action='store_true', help='Full duplex raw test; board must be in usb-ttl')
    parser.add_argument('--peer', help='USART1 adapter port for --bridge-test; --port is native USB')
    parser.add_argument('--size', type=int, help='Payload bytes; benchmark 0..64, bridge 1..4096 (defaults 64/256)')
    parser.add_argument('--can-performance', action='store_true', help='CAN Monitor external roundtrip benchmark')
    parser.add_argument('--serial-baseline', action='store_true', help='Serial ECHO baseline with the same test engine')
    parser.add_argument('--interval-ms', type=float, default=10)
    parser.add_argument('--timeout-ms', type=float, default=200)
    parser.add_argument('--window', type=int, default=1)
    parser.add_argument('--request-id', type=lambda value: int(value, 16), default=0x6e0)
    parser.add_argument('--reply-id', type=lambda value: int(value, 16), default=0x6e1)
    parser.add_argument('--output', help='Performance result JSON path; CSV is exported alongside')
    args = parser.parse_args(argv)
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.list:
        for port in list_ports.comports():
            print(f"{port.device}\t{port.description}\t{port.hwid}")
        return 0
    if sum((args.self_test, args.terminal, args.binary_test, args.benchmark, args.bridge_test,
            args.can_performance, args.serial_baseline)) > 1:
        parser.error('Choose one diagnostic mode')
    if args.benchmark and args.size is not None and not 0 <= args.size <= 64:
        parser.error('--benchmark --size must be 0..64')
    if args.bridge_test and (not args.peer or (args.size is not None and not 1 <= args.size <= 4096)):
        parser.error('--bridge-test requires --peer and size 1..4096')
    if args.self_test or args.terminal or args.binary_test or args.benchmark or args.bridge_test or args.can_performance or args.serial_baseline:
        if not args.port:
            parser.error("--port is required")
        try:
            if args.can_performance or args.serial_baseline:
                config = PerformanceConfig(target='serial' if args.serial_baseline else 'can', rounds=args.rounds,
                    interval_ms=args.interval_ms, timeout_ms=args.timeout_ms, window=args.window,
                    request_id=args.request_id, reply_id=args.reply_id)
                config.validate()
                with serial.Serial(args.port, BAUD, timeout=.01, write_timeout=1, exclusive=True) as port:
                    result = run_performance(port, config)
                if args.output:
                    export_performance(result, args.output)
                import json
                print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False, indent=2))
                return int(bool(result['cancelled'] or result['error'] or result['restore_errors'] or result['summary']['missing']))
            elif args.binary_test:
                binary_test(args.port, args.rounds, args.can_loopback)
            elif args.benchmark:
                benchmark(args.port, args.rounds, args.size if args.size is not None else 64)
            elif args.bridge_test:
                transparent_test(args.port, args.peer, args.rounds, args.size if args.size is not None else 256)
            elif args.self_test:
                self_test(args.port, args.rounds, args.can_loopback)
            else:
                terminal(args.port)
        except (serial.SerialException, TimeoutError, OSError, ValueError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
    else:
        gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
