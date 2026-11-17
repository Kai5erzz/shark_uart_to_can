"""ASCII long-text test panel, using the shared serial connection exclusively."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from raw_serial_test import Config, MAX_BYTES, ascii_preview, export, long_text


class RawTestPanel(ttk.Frame):
    def __init__(self, parent, start, stop):
        super().__init__(parent, padding=12)
        self.start_test, self.stop_test = start, stop
        self.result = None
        self.running = False
        self.passive = bytearray()
        self.passive_count = 0
        ttk.Label(self, text='USART2 RAW 双向长文本测试', font=('Sans', 17, 'bold')).pack(anchor='w')
        ttk.Label(self, text='上方串口选择接在USART2（PA2 TX / PA3 RX）的USB-TTL适配器，921600 8N1；\n'
                  'Monitor选择板上ID（默认001）并开启RAW原样回传；助手逐字节比对完整往返。',
                  foreground='#526276').pack(anchor='w', pady=(5, 10))
        controls = ttk.Frame(self)
        controls.pack(fill='x')
        self.values = {}
        self.entries = []
        for title, key, value in [('生成字节', 'size', '16384'), ('写入块', 'chunk', '32'),
                                  ('间隔ms', 'interval', '5'), ('超时ms', 'timeout', '2000')]:
            ttk.Label(controls, text=title).pack(side='left', padx=(0, 4))
            var = self.values[key] = tk.StringVar(value=value)
            entry = ttk.Entry(controls, textvariable=var, width=8)
            entry.pack(side='left', padx=(0, 12))
            self.entries.append(entry)
        self.generate_button = ttk.Button(controls, text='生成长文本', command=self.generate)
        self.generate_button.pack(side='left')
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=8)
        self.start_button = ttk.Button(actions, text='开始RAW双向测试', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='停止RAW测试', command=stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        self.save_button = ttk.Button(actions, text='导出结果 / 收发文本', command=self.save, state='disabled')
        self.save_button.pack(side='left')
        self.compare_button = ttk.Button(actions, text='比对独立反向接收', command=self.compare_passive)
        self.compare_button.pack(side='left', padx=8)
        self.clear_button = ttk.Button(actions, text='清空接收', command=self.clear_received)
        self.clear_button.pack(side='left')
        self.status = tk.StringVar(value='未开始；只发送下方文本本身，不添加SHARK协议头、校验或额外换行。')
        ttk.Label(self, textvariable=self.status, wraplength=1090).pack(anchor='w', pady=5)
        body = ttk.Panedwindow(self, orient='vertical')
        body.pack(fill='both', expand=True)
        tx = ttk.LabelFrame(body, text='发送ASCII原文（可编辑，最多1MiB）')
        rx = ttk.LabelFrame(body, text='接收原文（显示最近16KiB，完整数据可导出）')
        body.add(tx, weight=1)
        body.add(rx, weight=1)
        self.source = ScrolledText(tx, height=8, wrap='word', font=('Monospace', 10))
        self.source.pack(fill='both', expand=True)
        self.received = ScrolledText(rx, height=8, wrap='word', font=('Monospace', 10), state='disabled')
        self.received.pack(fill='both', expand=True)
        ttk.Label(self, text='CAN分包仅承载原始字节；按顺序拼接，不恢复串口设备原有消息边界。PASS要求全部字节一致。',
                  foreground='#64748b').pack(anchor='w', pady=(8, 0))
        self.source.insert('1.0', long_text(16384))

    def generate(self):
        try:
            text = long_text(int(self.values['size'].get()))
            self.source.delete('1.0', 'end')
            self.source.insert('1.0', text)
        except ValueError as exc:
            messagebox.showerror('文本设置错误', str(exc))

    def start(self):
        try:
            payload = self.source.get('1.0', 'end-1c').encode('ascii')
            config = Config(payload=payload, chunk_size=int(self.values['chunk'].get()),
                            interval_ms=float(self.values['interval'].get()),
                            timeout_ms=float(self.values['timeout'].get()))
            config.validate()
            self.start_test(config)
        except (ValueError, UnicodeError) as exc:
            messagebox.showerror('RAW测试设置错误', '请使用ASCII文本。' + str(exc))

    def set_running(self, value):
        self.running = value
        for widget in [self.start_button, self.generate_button, self.compare_button, self.clear_button, *self.entries]:
            widget.configure(state='disabled' if value else 'normal')
        self.source.configure(state='disabled' if value else 'normal')
        self.stop_button.configure(state='normal' if value else 'disabled')
        self.save_button.configure(state='normal' if not value and self.result else 'disabled')
        if value:
            self.passive.clear()
            self.passive_count = 0
            self.status.set('正在发送与比对原始数据…')

    def receive_passive(self, data):
        if self.running:
            return
        self.passive_count += len(data)
        self.passive.extend(data)
        del self.passive[:-MAX_BYTES]
        self.update_progress(dict(sent=0, total=0, received=self.passive_count, elapsed_s=0,
                                  preview=ascii_preview(self.passive[-16384:])))
        self.status.set(f'独立反向测试 · 已从USART2接收 {self.passive_count} B')

    def clear_received(self):
        self.passive.clear()
        self.passive_count = 0
        self.update_progress(dict(sent=0, total=0, received=0, elapsed_s=0, preview=''))
        self.status.set('接收已清空；独立反向测试时，将预期文本放在发送原文框，再从Monitor发送相同文本。')

    def compare_passive(self):
        try:
            expected = self.source.get('1.0', 'end-1c').encode('ascii')
            if not expected:
                raise ValueError('请在发送原文框填写预期接收的ASCII文本')
            passed = self.passive_count == len(expected) and bytes(self.passive) == expected
            if passed:
                self.status.set(f'PASS：CAN→USART2独立反向接收逐字节一致 · {len(expected)} B')
            else:
                offset = next((i for i, (a, b) in enumerate(zip(self.passive, expected)) if a != b),
                              min(len(self.passive), len(expected)))
                self.status.set(f'FAIL：反向接收不同或不完整 · 预期 {len(expected)} B / 实收 {self.passive_count} B · 首个差异 {offset}')
        except (ValueError, UnicodeError) as exc:
            messagebox.showerror('反向比对设置错误', str(exc))

    def update_progress(self, data):
        self.status.set(f'发送 {data["sent"]}/{data["total"]} B · 回传 {data["received"]} B · '
                        f'耗时 {data["elapsed_s"]:.2f} s')
        self.received.configure(state='normal')
        self.received.delete('1.0', 'end')
        self.received.insert('1.0', data['preview'])
        self.received.see('end')
        self.received.configure(state='disabled')

    def finish(self, result):
        self.result = result
        self.update_progress(result)
        self.set_running(False)
        label = 'PASS：逐字节一致' if result['passed'] else '已取消' if result['cancelled'] else 'FAIL：' + (result['error'] or '数据不完整')
        self.status.set(label + f' · 发送 {result["sent"]} B / 回传 {result["received"]} B · {result["elapsed_s"]:.2f} s')

    def save(self):
        if self.result:
            name = filedialog.asksaveasfilename(defaultextension='.json', initialfile='shark_uart2_raw.json')
            if name:
                try:
                    export(self.result, name)
                except OSError as exc:
                    messagebox.showerror('导出失败', str(exc))
