"""Small Chinese Linux UI for CAN1 capture and margin investigation."""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "tools.can_monitor"

from .backends import DEFAULT_SDK, DemoBackend, NativeBackend, SocketBackend, scan_native
from .core import Capture
from .serial_backend import SerialBackend, scan_serial
from .echo import EchoResponder
from .raw_stream import RawCANStream
from .raw_ui import RawMonitorPanel


def discover():
    result = {"native": 0, "native_error": None, "ports": [], "serial_error": None}
    try:
        result["ports"] = scan_serial()
    except Exception as exc:
        result["serial_error"] = str(exc)
    try:
        result["native"] = scan_native()
    except Exception as exc:
        result["native_error"] = str(exc)
    return result


def discovery_text(result):
    ports = result["ports"]
    text = f"原生 USB：{result['native']} 个；USB 串口：{len(ports)} 个"
    if ports:
        text += " · " + "；".join(
            f"{p['port']} [{p['vid']:04X}:{p['pid']:04X}]" if p['vid'] is not None and p['pid'] is not None
            else p['port'] for p in ports)
        text += "。串口型号/协议需连接时确认。"
    for key in ("native_error", "serial_error"):
        if result[key]:
            text += " · " + result[key]
    return text


class App:
    def __init__(self, root, demo=False, output=None, autoconnect=False, port=None, echo=False):
        self.root = root
        root.title("CAN1 观察台 · 达妙 USB2FDCAN")
        root.geometry("1250x850")
        root.minsize(1050, 720)
        self.backend = self.capture = None
        self.busy = False
        self.closed = False
        self.demo = demo
        self.autoconnect = autoconnect
        self.last_snapshot = None
        self.echo = EchoResponder()
        self.raw_stream = RawCANStream()
        self.echo_on_connect = echo
        self.echo_enabled = tk.BooleanVar(value=False)
        self.echo_request = tk.StringVar(value='6E0')
        self.echo_reply = tk.StringVar(value='6E1')
        self.echo_status = tk.StringVar(value='回传关闭')
        self.backend_choice = tk.StringVar(value="演示数据" if demo else "自动识别")
        self.device = tk.StringVar(value="0")
        self.channel = tk.StringVar(value="0")
        self.interface = tk.StringVar(value="can0")
        self.bitrate = tk.StringVar(value="1000000")
        self.port = tk.StringVar(value=port or "")
        self.folder = tk.StringVar(value=str(output or Path.home() / "sample/can1"))
        self.filter = tk.StringVar()
        self.status = tk.StringVar(value="未连接 · 接入分析仪后扫描设备，单通道选择 CH0")
        self.record = tk.BooleanVar(value=True)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()
        if demo:
            root.after(200, self.connect)
        else:
            root.after(200, self.scan)

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=27, font=("Noto Sans CJK SC", 10))
        style.configure("Treeview.Heading", font=("Noto Sans CJK SC", 10, "bold"))
        style.configure("TLabel", font=("Noto Sans CJK SC", 10))
        style.configure("TButton", padding=6)
        main = ttk.Frame(self.root, padding=18)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(7, weight=1)
        ttk.Label(main, text="CAN1 观察台", font=("Noto Sans CJK SC", 23, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="逐帧记录  /  电机通信节奏  /  总线占用估算", foreground="#576477").grid(row=1, column=0, sticky="w", pady=(2, 14))
        controls = ttk.Frame(main)
        controls.grid(row=2, column=0, sticky="ew")
        for label, variable, values, width in (
            ("接口", self.backend_choice, ("自动识别", "达妙原厂 SDK", "达妙串口 USB2CAN", "SocketCAN", "演示数据"), 17),
            ("设备序号", self.device, None, 4),
            ("CH", self.channel, ("0", "1", "2", "3"), 3),
            ("bit/s", self.bitrate, ("1000000", "500000", "250000", "125000"), 10),
            ("网口", self.interface, None, 7),
        ):
            ttk.Label(controls, text=label).pack(side="left", padx=(0, 5))
            widget = (ttk.Combobox(controls, textvariable=variable, values=values,
                                  state="readonly", width=width) if values else
                      ttk.Entry(controls, textvariable=variable, width=width))
            widget.pack(side="left", padx=(0, 12))
        self.scan_button = ttk.Button(controls, text="扫描设备", command=self.scan)
        self.scan_button.pack(side="left")
        self.connect_button = ttk.Button(controls, text="连接并记录", command=self.connect)
        self.connect_button.pack(side="right")
        self.disconnect_button = ttk.Button(controls, text="停止并保存", command=self.disconnect, state="disabled")
        self.disconnect_button.pack(side="right", padx=8)
        files = ttk.Frame(main)
        files.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        ttk.Label(files, text="串口").pack(side="left")
        self.port_box = ttk.Combobox(files, textvariable=self.port, width=17)
        self.port_box.pack(side="left", padx=(6, 12))
        ttk.Checkbutton(files, text="自动保存原始 CSV", variable=self.record).pack(side="left")
        ttk.Entry(files, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(files, text="选择目录", command=self.choose_folder).pack(side="left")
        ttk.Button(files, text="打开目录", command=self.open_folder).pack(side="left", padx=(8, 0))
        self.status_label = ttk.Label(main, textvariable=self.status, foreground="#176b53", wraplength=1150)
        self.status_label.grid(row=4, column=0, sticky="w", pady=(4, 10))
        cards = ttk.Frame(main)
        cards.grid(row=5, column=0, sticky="ew", pady=(0, 12))
        self.card_text = []
        for title in ("收到帧数", "估算占用 · 近 1 s", "估算占用 · 近 100 ms", "应用队列丢帧 / 错误事件"):
            box = ttk.LabelFrame(cards, text=title, padding=(12, 8))
            box.pack(side="left", fill="x", expand=True, padx=(0, 8))
            value = tk.StringVar(value="—")
            self.card_text.append(value)
            ttk.Label(box, textvariable=value, font=("DejaVu Sans", 20, "bold")).pack(anchor="w")
        toolbar = ttk.Frame(main)
        toolbar.grid(row=6, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(toolbar, text="分组统计 · 0x10 自动按电机编号拆分").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.filter, width=22).pack(side="right")
        ttk.Label(toolbar, text="显示筛选（不影响日志） ").pack(side="right")
        columns = ("group", "count", "fps", "last", "p99", "max", "age", "data")
        self.pages = ttk.Notebook(main)
        self.pages.grid(row=7, column=0, sticky='nsew')
        traffic = ttk.Frame(self.pages)
        self.pages.add(traffic, text='CAN帧 / 统计')
        traffic.columnconfigure(0, weight=1)
        traffic.rowconfigure(0, weight=1)
        self.raw_panel = RawMonitorPanel(self.pages, self.raw_stream, lambda: self.backend)
        self.pages.add(self.raw_panel, text='USART2 RAW / ASCII 双向测试')
        table = ttk.Frame(traffic)
        table.grid(row=0, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=11)
        labels = ("通道 / ID / 电机", "帧数", "近 1s 帧/s", "间隔 ms", "P99 ms", "最大 ms", "距上帧 ms", "最新数据")
        widths = (280, 65, 90, 80, 80, 80, 95, 240)
        for col, text, width in zip(columns, labels, widths):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w" if col in ("group", "data") else "e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        ttk.Label(traffic, text="最近原始帧 · 界面仅保留最近 200 帧，CSV 记录全部已接收帧").grid(row=1, column=0, sticky="w", pady=(10, 5))
        self.raw = tk.Text(traffic, height=5, wrap="none", font=("DejaVu Sans Mono", 10),
                           background="#142336", foreground="#d8e8f4", relief="flat", padx=10, pady=8)
        self.raw.grid(row=2, column=0, sticky="ew")
        ttk.Label(main, text="间隔使用电脑接收时间，含 USB 批量上传抖动；设备时间戳原样入 CSV。P99 为最近 10000 个间隔。\n"
                  "占用按收到的经典 CAN 帧重建，包含填充位；不含错误重发及 CAN FD。原厂接口按参与 ACK 处理。",
                  foreground="#667085").grid(row=8, column=0, sticky="w", pady=(9, 0))
        for widget in main.winfo_children():
            row = int(widget.grid_info()['row'])
            if row >= 4:
                widget.grid_configure(row=row + 1)
        main.rowconfigure(7, weight=0)
        main.rowconfigure(8, weight=1)
        echo = ttk.Frame(main)
        echo.grid(row=4, column=0, sticky='ew', pady=(2, 8))
        ttk.Checkbutton(echo, text='SHARK 测试帧立即回传', variable=self.echo_enabled,
                        command=self.configure_echo).pack(side='left')
        for caption, variable in [('请求 ID(HEX)', self.echo_request), ('回复 ID(HEX)', self.echo_reply)]:
            ttk.Label(echo, text=caption).pack(side='left', padx=(10, 4))
            ttk.Entry(echo, textvariable=variable, width=7).pack(side='left')
        ttk.Button(echo, text='应用回传设置', command=self.configure_echo).pack(side='left', padx=8)
        ttk.Label(echo, textvariable=self.echo_status).pack(side='left', padx=5)

    def configure_echo(self):
        try:
            if self.echo_enabled.get() and (not self.backend or not hasattr(self.backend, 'send')):
                raise ValueError('请先连接支持发送的实际 CAN 接口')
            self.echo.configure(self.echo_enabled.get(), int(self.echo_request.get(), 16), int(self.echo_reply.get(), 16))
        except ValueError as exc:
            self.echo_enabled.set(False)
            self.echo.configure(False)
            messagebox.showerror('回传设置错误', str(exc))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder.get())
        if folder:
            self.folder.set(folder)

    def open_folder(self):
        path = Path(self.folder.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _async(self, work, done):
        self.busy = True
        self.scan_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.disconnect_button.configure(state="disabled")
        def task():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, str(exc)
            self.root.after(0, lambda: finish(result, error))
        def finish(result, error):
            self.busy = False
            self.scan_button.configure(state="disabled" if self.backend else "normal")
            self.connect_button.configure(state="disabled" if self.backend else "normal")
            self.disconnect_button.configure(state="normal" if self.backend else "disabled")
            done(result, error)
        threading.Thread(target=task, name="can-device", daemon=True).start()

    def scan(self):
        if self.backend or self.busy:
            return
        self.status.set("扫描原生 USB 和 CDC 串口设备中…")
        def done(result, error):
            if error:
                self.status.set(error)
                return
            self.port_box.configure(values=[p["port"] for p in result["ports"]])
            candidates = [p for p in result["ports"] if p["candidate"]]
            if self.port.get() not in [p["port"] for p in result["ports"]]:
                self.port.set(candidates[0]["port"] if candidates else "")
            self.status.set(discovery_text(result))
            if self.autoconnect:
                self.autoconnect = False
                self.connect()
        self._async(discover, done)

    def connect(self):
        if self.backend or self.busy:
            return
        try:
            bitrate = int(self.bitrate.get())
            channel, index = int(self.channel.get()), int(self.device.get())
            if bitrate <= 0 or index < 0:
                raise ValueError("设备编号和波特率无效")
            choice, interface, port = self.backend_choice.get(), self.interface.get(), self.port.get().strip()
            folder = Path(self.folder.get()).expanduser()
            path = folder / (datetime.now().strftime("can1_%Y%m%d_%H%M%S_%f") + ("_DEMO" if choice == "演示数据" else "") + ".csv") if self.record.get() else None
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        self.status.set("正在连接并配置接口…")
        def work():
            selected = choice
            selected_port = port
            if selected == "自动识别":
                found = discover()
                if found["native"]:
                    selected = "达妙原厂 SDK"
                else:
                    candidates = [p["port"] for p in found["ports"] if p["candidate"]]
                    if not selected_port and len(candidates) == 1:
                        selected_port = candidates[0]
                    if not selected_port:
                        raise RuntimeError(discovery_text(found) + "；请选择串口，或检查分析仪 USB 连接。")
                    selected = "达妙串口 USB2CAN"
            backend = (DemoBackend() if selected == "演示数据" else SocketBackend(interface)
                       if selected == "SocketCAN" else SerialBackend(selected_port, bitrate, channel)
                       if selected == "达妙串口 USB2CAN" else NativeBackend(index, channel, bitrate))
            capture = Capture(path, bitrate, backend.metadata)
            try:
                def submit(frame):
                    # Reply in the receive callback, before GUI polling / disk flush.
                    self.echo.handle(frame, backend.send if hasattr(backend, 'send') else None)
                    self.raw_stream.handle(frame, backend.send if hasattr(backend, 'send') else None)
                    capture.submit(frame)
                backend.start(submit)
                capture.metadata.update(backend.metadata)
            except Exception as exc:
                backend.close()
                capture.metadata.update(backend.metadata)
                capture.metadata["backend_failure"] = str(exc)
                capture.close()
                raise
            return backend, capture
        def done(result, error):
            if error:
                self.status.set("连接失败：" + error)
                self.status_label.configure(foreground="#b42318")
                return
            self.backend, self.capture = result
            if self.echo_on_connect:
                self.echo_on_connect = False
                self.echo_enabled.set(True)
                self.configure_echo()
            self.status_label.configure(foreground="#176b53")
            self.scan_button.configure(state="disabled")
            self.connect_button.configure(state="disabled")
            self.disconnect_button.configure(state="normal")
            self.status.set(("演示数据 · " if choice == "演示数据" else "接收中 · ") +
                            (str(path) if path else "本次未保存日志"))
            if hasattr(self.backend, "status_text"):
                self.serial_status()
        self._async(work, done)

    def serial_status(self):
        self.status.set(self.backend.status_text())
        self.status_label.configure(foreground="#176b53" if self.backend.metadata["can_rx_count"] else "#9a6700")

    def disconnect(self, after=None):
        if self.busy:
            return
        if not self.backend:
            if after:
                after()
            return
        backend, capture = self.backend, self.capture
        self.echo.configure(False)
        self.echo_enabled.set(False)
        self.raw_stream.disable_echo()
        self.raw_panel.echo_enabled.set(False)
        self.raw_panel.stop()
        self.status.set("停止接收并排空日志队列…")
        def work():
            backend.close()
            capture.metadata['echo'] = self.echo.snapshot()
            capture.metadata['raw_stream'] = self.raw_stream.snapshot()
            capture.metadata.update(backend.metadata)
            capture.metadata.update(kernel_dropped=getattr(backend, "kernel_dropped", None),
                                    backend_failure=getattr(backend, "failure", None),
                                    callback_errors=getattr(backend, "callback_errors", None))
            result = capture.close()
            result["backend_failure"] = getattr(backend, "failure", None)
            return result
        def done(result, error):
            self.backend = None
            self.scan_button.configure(state="normal")
            self.connect_button.configure(state="normal")
            self.disconnect_button.configure(state="disabled")
            if error:
                self.status.set("保存异常：" + error)
            else:
                self.last_snapshot = result
                warning = result.get("failure") or result.get("backend_failure")
                self.status.set(("记录结束，有异常：" + warning if warning else "已停止并保存") +
                                f" · {result['total']} 帧 · 队列丢帧 {result['queue_dropped']}" +
                                (f" · {capture.path}" if capture.path else ""))
            if after:
                after()
        self._async(work, done)

    @staticmethod
    def number(value):
        return "—" if value is None else f"{value:.3f}"

    def tick(self):
        if self.closed:
            return
        self.raw_panel.refresh()
        echo = self.echo.snapshot()
        self.echo_status.set(f'{"回传开启" if echo["enabled"] else "回传关闭"} · 收到 {echo["received"]} '
                             f'已提交 {echo["sent"]} 失败 {echo["errors"]}')
        if self.capture and not self.busy:
            result, frames = self.capture.snapshot()
            self.last_snapshot = result
            self.card_text[0].set(f"{result['total']:,}")
            self.card_text[1].set(f"{result['load_percent'][1000]:.1f}%")
            self.card_text[2].set(f"{result['load_percent'][100]:.1f}%")
            self.card_text[3].set(f"{result['queue_dropped']} / {result['errors']}")
            query = self.filter.get().strip().lower()
            visible = set()
            for row in result["streams"]:
                name = row["group"]
                if query and query not in (name + row["data"]).lower():
                    continue
                visible.add(name)
                values = (name, row["count"], f"{row['fps']:.0f}",
                          *(self.number(row[x]) for x in ("last_ms", "p99_ms", "max_ms", "age_ms")), row["data"])
                if self.tree.exists(name):
                    self.tree.item(name, values=values)
                else:
                    self.tree.insert("", "end", iid=name, values=values)
            for name in self.tree.get_children():
                if name not in visible:
                    self.tree.delete(name)
            lines = []
            for frame in frames[-45:]:
                if query and query not in (frame.group + frame.data.hex(" ")).lower():
                    continue
                elapsed = (frame.host_ns - self.capture.stats.start_ns) / 1e9
                lines.append(f"{elapsed:10.6f}  CH{frame.channel} {frame.direction:3}  "
                             f"{'E' if frame.extended else 'S'} {frame.can_id:08X}  "
                             f"DLC={frame.dlc:2}  {frame.data.hex(' ').upper()}")
            self.raw.configure(state="normal")
            self.raw.delete("1.0", "end")
            self.raw.insert("end", "\n".join(lines))
            self.raw.see("end")
            self.raw.configure(state="disabled")
            failure = result["failure"] or getattr(self.backend, "failure", None)
            if failure and self.backend:
                self.status.set(str(failure))
                self.disconnect()
            elif hasattr(self.backend, "status_text"):
                self.serial_status()
        self.root.after(250, self.tick)

    def close(self):
        if self.busy:
            self.root.after(200, self.close)
            return
        def finish():
            self.closed = True
            self.root.destroy()
        self.disconnect(after=finish)


def main():
    parser = argparse.ArgumentParser(description="Linux 达妙 CAN1 观察台")
    parser.add_argument("--demo", action="store_true", help="演示普通/跳跃总线流量")
    parser.add_argument("--scan", action="store_true", help="扫描达妙原生 USB 和 CDC 串口")
    parser.add_argument("--output", type=Path, help="日志目录")
    parser.add_argument("--connect", action="store_true", help="启动扫描后自动打开接口并等待数据")
    parser.add_argument("--port", help="USB 串口路径，例如 /dev/ttyACM0")
    parser.add_argument('--echo', action='store_true', help='连接后启用6E0→6E1测试帧回传')
    args = parser.parse_args()
    if args.scan:
        try:
            print(discovery_text(discover()))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    root = tk.Tk()
    App(root, demo=args.demo, output=args.output, autoconnect=args.connect, port=args.port, echo=args.echo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
