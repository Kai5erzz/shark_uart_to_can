import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from .raw_stream import ascii_text


class RawMonitorPanel(ttk.Frame):
    def __init__(self, parent, stream, backend):
        super().__init__(parent, padding=8)
        self.stream, self.get_backend = stream, backend
        self.last_revision = -1
        self.pending = None
        self.sending = False
        self.can_id = tk.StringVar(value='001')
        self.echo_enabled = tk.BooleanVar(value=False)
        controls = ttk.Frame(self)
        controls.pack(fill='x')
        ttk.Label(controls, text='板上ID（HEX）').pack(side='left')
        ttk.Entry(controls, textvariable=self.can_id, width=8).pack(side='left', padx=5)
        ttk.Checkbutton(controls, text='RAW测试：收到即同ID原样回传', variable=self.echo_enabled,
                        command=self.configure).pack(side='left', padx=8)
        ttk.Button(controls, text='应用ID', command=self.configure).pack(side='left')
        ttk.Button(controls, text='清空接收', command=stream.clear).pack(side='left', padx=8)
        ttk.Button(controls, text='导出接收字节', command=self.save).pack(side='left')
        self.status = tk.StringVar(value='按CAN接收顺序拼接数据；不在8字节帧之间插入换行。')
        ttk.Label(self, textvariable=self.status, wraplength=1120).pack(anchor='w', pady=5)
        self.received = ScrolledText(self, height=7, wrap='word', font=('Monospace', 10), state='disabled')
        self.received.pack(fill='both', expand=True)
        sender = ttk.Frame(self)
        sender.pack(fill='x', pady=5)
        ttk.Label(sender, text='独立反向测试：下方ASCII → CAN → USART2').pack(side='left')
        self.interval = tk.StringVar(value='2')
        ttk.Label(sender, text='帧间隔ms').pack(side='left', padx=(12, 4))
        ttk.Entry(sender, textvariable=self.interval, width=5).pack(side='left')
        self.send_button = ttk.Button(sender, text='发送ASCII到USART2', command=self.send)
        self.send_button.pack(side='left', padx=8)
        ttk.Button(sender, text='停止发送', command=self.stop).pack(side='left')
        self.tx_status = tk.StringVar(value='未发送')
        ttk.Label(sender, textvariable=self.tx_status).pack(side='left', padx=6)
        self.source = ScrolledText(self, height=3, wrap='word', font=('Monospace', 10))
        self.source.pack(fill='x')
        self.source.insert('1.0', 'Hello from CAN Monitor!\n')
        ttk.Label(self, text='仅解析标准数据帧；TX报告不会触发回传。显示保留最近64KiB，完整原始帧可记录CSV。\n'
                  '往返测试需开启同ID回传；独立反向测试可关闭回传。非ASCII控制字节以\\xNN显示。',
                  foreground='#64748b').pack(anchor='w', pady=(5, 0))

    def configure(self):
        try:
            backend = self.get_backend()
            if self.echo_enabled.get() and (not backend or not hasattr(backend, 'send')):
                raise ValueError('请先连接支持发送的CAN接口')
            self.stream.configure(int(self.can_id.get(), 16), self.echo_enabled.get())
        except ValueError as exc:
            self.echo_enabled.set(False)
            self.stream.disable_echo()
            messagebox.showerror('RAW设置错误', str(exc))

    def refresh(self):
        snapshot = self.stream.snapshot(with_data=True)
        backend = self.get_backend()
        statuses = getattr(backend, 'metadata', {}).get('adapter_status_counts', {})
        tx_failures = statuses.get('0x02', 0)
        revision = (snapshot['revision'], tx_failures)
        if revision == self.last_revision:
            return
        self.last_revision = revision
        self.status.set(f'ID {snapshot["can_id"]:03X} · RX {snapshot["frames"]}帧 / {snapshot["bytes"]} B · '
                        f'回传提交 {snapshot["echo_sent"]}帧 · 提交失败 {snapshot["errors"]} · '
                        f'适配器发送失败 {tx_failures} · '
                        f'显示裁剪 {snapshot["display_trimmed_bytes"]} B' +
                        (f' · {snapshot["last_error"]}' if snapshot['last_error'] else ''))
        self.received.configure(state='normal')
        self.received.delete('1.0', 'end')
        self.received.insert('1.0', ascii_text(snapshot['data']))
        self.received.see('end')
        self.received.configure(state='disabled')

    def send(self):
        if self.sending:
            return
        try:
            backend = self.get_backend()
            if not backend or not hasattr(backend, 'send'):
                raise ValueError('请先连接支持发送的CAN接口')
            data = self.source.get('1.0', 'end-1c').encode('ascii')
            identifier, interval = int(self.can_id.get(), 16), int(self.interval.get())
            if not 0 <= identifier <= 0x7ff or not 1 <= interval <= 60000 or not 1 <= len(data) <= 1048576:
                raise ValueError('ID范围000～7FF，间隔1～60000ms，ASCII文本1～1048576字节')
        except (ValueError, UnicodeError) as exc:
            messagebox.showerror('发送设置错误', str(exc))
            return
        self.sending = True
        self.send_button.configure(state='disabled')
        self.source.configure(state='disabled')
        offset = 0
        def tick():
            nonlocal offset
            self.pending = None
            if not self.sending:
                return
            try:
                if self.get_backend() is not backend:
                    raise OSError('CAN接口已断开')
                part = data[offset:offset + 8]
                backend.send(identifier, part)
                offset += len(part)
                self.tx_status.set(f'已提交 {offset}/{len(data)} B')
                if offset == len(data):
                    self.stop()
                else:
                    self.pending = self.after(interval, tick)
            except Exception as exc:
                self.stop()
                self.tx_status.set('发送失败：' + str(exc))
        tick()

    def stop(self):
        self.sending = False
        if self.pending is not None:
            self.after_cancel(self.pending)
            self.pending = None
        self.send_button.configure(state='normal')
        self.source.configure(state='normal')

    def save(self):
        name = filedialog.asksaveasfilename(defaultextension='.txt', initialfile='can_raw_ascii.txt')
        if name:
            try:
                with open(name, 'wb') as output:
                    output.write(self.stream.snapshot(with_data=True)['data'])
            except OSError as exc:
                messagebox.showerror('导出失败', str(exc))
