"""SHARK performance tab; serial I/O and timing run outside Tk."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from can_performance import Config, export


class PerformancePanel(ttk.Frame):
    def __init__(self, parent, start, stop):
        super().__init__(parent, padding=14)
        self.start_test, self.stop_test = start, stop
        self.result = None
        self.running = False
        ttk.Label(self, text='CAN 往返性能测试', font=('Sans', 18, 'bold')).pack(anchor='w')
        self.path_text = tk.StringVar(value='主机 → SHARK → CAN → CAN Monitor 回传 → CAN → SHARK → 主机')
        ttk.Label(self, textvariable=self.path_text, foreground='#526276').pack(anchor='w', pady=(4, 12))
        controls = ttk.Frame(self)
        controls.pack(fill='x')
        self.preset = tk.StringVar(value='低载 RTT')
        ttk.Label(controls, text='场景').pack(side='left')
        self.choice = ttk.Combobox(controls, textvariable=self.preset,
            values=('低载 RTT', '持续吞吐', '串口回显基线'), state='readonly', width=15)
        self.choice.pack(side='left', padx=6)
        self.choice.bind('<<ComboboxSelected>>', self.choose_preset)
        self.values = {}
        self.entries = []
        for caption, key, initial, width in [('样本', 'rounds', '1000', 7), ('间隔 ms', 'interval_ms', '10', 7),
                                             ('超时 ms', 'timeout_ms', '200', 7), ('窗口', 'window', '1', 5)]:
            ttk.Label(controls, text=caption).pack(side='left', padx=(8, 3))
            variable = tk.StringVar(value=initial)
            self.values[key] = variable
            entry = ttk.Entry(controls, textvariable=variable, width=width)
            entry.pack(side='left')
            self.entries.append(entry)
        ids = ttk.Frame(self)
        ids.pack(fill='x', pady=10)
        for caption, key, initial in [('请求 ID(HEX)', 'request_id', '6E0'), ('回复 ID(HEX)', 'reply_id', '6E1')]:
            ttk.Label(ids, text=caption).pack(side='left', padx=(0, 5))
            variable = tk.StringVar(value=initial)
            self.values[key] = variable
            entry = ttk.Entry(ids, textvariable=variable, width=8)
            entry.pack(side='left', padx=(0, 16))
            self.entries.append(entry)
        self.start_button = ttk.Button(ids, text='开始性能测试', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(ids, text='停止测试', command=self.stop_test, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        self.export_button = ttk.Button(ids, text='导出 JSON / CSV', command=self.save, state='disabled')
        self.export_button.pack(side='left')
        cards = ttk.Frame(self)
        cards.pack(fill='x', pady=5)
        self.cards = {}
        for title, key in [('平均 RTT / ms', 'mean_ms'), ('P95 / ms', 'p95_ms'), ('P99 / ms', 'p99_ms'),
                           ('最大 RTT / ms', 'max_ms'), ('有效接收 / 帧每秒', 'rx_fps')]:
            frame = ttk.LabelFrame(cards, text=title, padding=10)
            frame.pack(side='left', fill='x', expand=True, padx=(0, 7))
            variable = tk.StringVar(value='—')
            self.cards[key] = variable
            ttk.Label(frame, textvariable=variable, font=('Sans', 20, 'bold')).pack(anchor='w')
        self.status = tk.StringVar(value='连接 SHARK 后开始；CAN Monitor 需启用 6E0→6E1 回传，CAN 波特率 1 Mbit/s。')
        ttk.Label(self, textvariable=self.status, wraplength=1080).pack(anchor='w', pady=(10, 5))
        self.details = tk.StringVar(value='—')
        ttk.Label(self, textvariable=self.details, wraplength=1080).pack(anchor='w', pady=(0, 8))
        self.canvas = tk.Canvas(self, height=150, background='#142336', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.samples = []
        ttk.Label(self, text='曲线显示最近 200 个有效 RTT。计时从串口写入前到完整回复接收后，使用接收线程时间戳；\n'
                  '包含主机调度、USB/UART、双程 CAN 及 Monitor 回传开销。预热 10 帧不计入统计。',
                  foreground='#64748b').pack(anchor='w', pady=(8, 0))

    def choose_preset(self, _=None):
        throughput = self.preset.get() == '持续吞吐'
        self.values['interval_ms'].set('0' if throughput else '10')
        self.values['window'].set('16' if throughput else '1')
        self.path_text.set('主机 → SHARK 二进制回显 → 主机（不经过 CAN Monitor）'
                           if self.preset.get() == '串口回显基线' else
                           '主机 → SHARK → CAN → CAN Monitor 回传 → CAN → SHARK → 主机')

    def start(self):
        try:
            config = Config(target='serial' if self.preset.get() == '串口回显基线' else 'can',
                            rounds=int(self.values['rounds'].get()),
                            interval_ms=float(self.values['interval_ms'].get()),
                            timeout_ms=float(self.values['timeout_ms'].get()), window=int(self.values['window'].get()),
                            request_id=int(self.values['request_id'].get(), 16),
                            reply_id=int(self.values['reply_id'].get(), 16))
            config.validate()
            self.start_test(config)
        except ValueError as exc:
            messagebox.showerror('性能测试设置错误', str(exc))

    def set_running(self, running):
        self.running = running
        self.start_button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        self.export_button.configure(state='normal' if not running and self.result else 'disabled')
        self.choice.configure(state='disabled' if running else 'readonly')
        for entry in self.entries:
            entry.configure(state='disabled' if running else 'normal')
        if running:
            self.status.set('正在准备设备并预热…')

    def update_progress(self, event):
        summary = event['summary']
        for key, variable in self.cards.items():
            value = summary[key]
            variable.set('—' if value is None else f'{value:.3f}' if key != 'rx_fps' else f'{value:.0f}')
        self.status.set(f'{event["phase"]} · 发送 {summary["sent"]} · 有效回复 {summary["received"]} · '
                        f'超时 {summary["timeouts"]} · 入队拒绝 {summary["rejected"]} · 取消 {summary["cancelled"]} · '
                        f'未完成 {summary["missing"]} ({summary["loss_percent"]:.2f}%)')
        def number(key):
            return '—' if summary[key] is None else f'{summary[key]:.3f}'
        self.details.set(f'最小 {number("min_ms")} ms · P50 {number("p50_ms")} ms · '
                         f'标准差 {number("stddev_ms")} ms · 相邻 RTT 抖动 {number("jitter_ms")} ms · '
                         f'返回有效载荷 {summary["payload_Bps"]:.0f} B/s · '
                         f'重复 {summary["duplicates"]} · 迟到 {summary["late"]}')
        self.samples = summary['samples_ms']
        self.draw()

    def finish(self, result):
        self.result = result
        self.update_progress(dict(phase='已取消' if result['cancelled'] else '完成', summary=result['summary']))
        self.set_running(False)
        errors = ([result['error']] if result['error'] else []) + result['restore_errors']
        if errors:
            self.status.set('测试异常：' + '；'.join(errors))

    def draw(self):
        self.canvas.delete('all')
        if not self.samples:
            self.canvas.create_text(20, 20, anchor='nw', text='等待有效回传…', fill='#a7bfdc')
            return
        width, height = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        top = max(max(self.samples) * 1.1, .1)
        points = []
        for index, value in enumerate(self.samples):
            points.extend((55 + (width - 70) * index / max(1, len(self.samples) - 1),
                           height - 25 - (height - 50) * value / top))
        self.canvas.create_text(7, 15, anchor='nw', text=f'{top:.2f} ms', fill='#a7bfdc')
        self.canvas.create_text(7, height - 25, anchor='nw', text='0 ms', fill='#a7bfdc')
        self.canvas.create_line(50, 20, 50, height - 25, width - 10, height - 25, fill='#38506b')
        if len(points) >= 4:
            self.canvas.create_line(*points, fill='#52dac5', width=2)
        else:
            x, y = points
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill='#52dac5')

    def save(self):
        if self.result:
            path = filedialog.asksaveasfilename(defaultextension='.json', initialfile='shark_can_performance.json',
                                              filetypes=[('JSON + CSV', '*.json')])
            if path:
                try:
                    export(self.result, path)
                except OSError as exc:
                    messagebox.showerror('导出失败', str(exc))
