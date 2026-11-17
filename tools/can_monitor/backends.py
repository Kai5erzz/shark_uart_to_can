"""DM_DeviceSDK v1.1.0, SocketCAN and simulation backends."""
from __future__ import annotations

import ctypes as C
import os
import socket
import struct
import threading
import time
from pathlib import Path

from .core import DLC_LENGTHS, Frame, MOTORS

HERE = Path(__file__).resolve().parent
DEFAULT_SDK = HERE / "vendor" / "libdm_device.so"


class RxHead(C.LittleEndianStructure):
    # dmcan.h uses pragma pack(1). Integer masks avoid ctypes bitfield ABI ambiguity.
    _pack_ = 1
    _fields_ = [("id_flags", C.c_uint32), ("time_stamp", C.c_uint64),
                ("channel", C.c_uint8), ("flags_dlc", C.c_uint8),
                ("reserved", C.c_uint16)]


class RxFrame(C.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [("head", RxHead), ("payload", C.c_uint8 * 64)]


class BaudInfo(C.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [("channel", C.c_uint8), ("canfd", C.c_bool),
                ("can_baudrate", C.c_uint32), ("canfd_baudrate", C.c_uint32),
                ("can_sp", C.c_float), ("canfd_sp", C.c_float)]


Callback = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(RxFrame))
assert C.sizeof(RxHead) == 16 and C.sizeof(RxFrame) == 80 and C.sizeof(BaudInfo) == 18


def decode_sdk(raw: RxFrame, direction="RX", host_ns=None, wall_ns=None):
    head = raw.head
    bits, flags = head.id_flags, head.flags_dlc
    dlc = flags >> 4
    fd, remote = bool(flags & 1), bool(bits & 0x80000000)
    length = DLC_LENGTHS[dlc] if fd else min(dlc, 8)
    return Frame(host_ns if host_ns is not None else time.monotonic_ns(),
                 wall_ns if wall_ns is not None else time.time_ns(),
                 bits & 0x1FFFFFFF, b"" if remote else bytes(raw.payload[:length]),
                 head.channel, bool(bits & 0x40000000), remote, fd, bool(flags & 4),
                 direction, head.time_stamp, dlc, bool(bits & 0x20000000), bool(flags & 8))


def load_sdk(path=DEFAULT_SDK):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError("未找到官方 Linux SDK。先运行：python3 tools/can_monitor/setup_sdk.py")
    # Scope the optional newer C++ runtime to this process; never change system libraries.
    runtime = os.environ.get("DMCAN_CXX_RUNTIME")
    if not runtime:
        candidates = []
        if os.environ.get("CONDA_PREFIX"):
            candidates.append(Path(os.environ["CONDA_PREFIX"]) / "lib/libstdc++.so.6")
        candidates += [Path.home() / "miniconda3/lib/libstdc++.so.6",
                       Path.home() / "anaconda3/lib/libstdc++.so.6"]
        # This SDK binary requires GLIBCXX_3.4.32; older Ubuntu may need a local runtime.
        for candidate in candidates:
            if candidate.is_file() and b"GLIBCXX_3.4.32" in candidate.read_bytes():
                runtime = str(candidate)
                break
    if runtime:
        C.CDLL(runtime, mode=C.RTLD_GLOBAL)
    local_usb = HERE / "vendor/runtime/lib/libusb-1.0.so.0"
    if local_usb.exists():
        C.CDLL(str(local_usb), mode=C.RTLD_GLOBAL)
    try:
        lib = C.CDLL(str(path))
    except OSError as exc:
        raise RuntimeError(f"官方 SDK 加载失败：{exc}\n可用 DMCAN_CXX_RUNTIME 指定兼容的 libstdc++.so.6。") from exc
    signatures = {
        "dmcan_context_create": ([C.POINTER(C.c_void_p)], None),
        "dmcan_context_destroy": ([C.c_void_p], None),
        "dmcan_find_devices": ([C.c_void_p], C.c_int),
        "dmcan_show_all_devices": ([C.c_void_p], None),
        "dmcan_device_get": ([C.c_void_p, C.POINTER(C.c_void_p), C.c_int], C.c_bool),
        "dmcan_device_open": ([C.c_void_p], C.c_bool),
        "dmcan_device_close": ([C.c_void_p], None),
        "dmcan_device_enable_channel": ([C.c_void_p, C.c_uint8], C.c_bool),
        "dmcan_device_disable_channel": ([C.c_void_p, C.c_uint8], C.c_bool),
        "dmcan_device_get_channel_baudrate": ([C.c_void_p, C.c_uint8, C.POINTER(BaudInfo)], C.c_bool),
        "dmcan_device_set_channel_baudrate": ([C.c_void_p, C.c_uint8, BaudInfo], C.c_bool),
        "dmcan_device_hook_recv_callback": ([C.c_void_p, Callback], None),
        "dmcan_device_hook_err_callback": ([C.c_void_p, Callback], None),
    }
    for name, (args, ret) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, ret
    return lib


class NativeBackend:
    # The send API is bound lazily when explicit echo is enabled.
    def __init__(self, index=0, channel=0, bitrate=1_000_000, sdk=DEFAULT_SDK):
        self.index, self.channel, self.bitrate = index, channel, bitrate
        self.lib = load_sdk(sdk)
        self.ctx, self.device = C.c_void_p(), C.c_void_p()
        self.opened = self.enabled = False
        self.closing = False
        self.failure = None
        self.callback_errors = 0
        self.metadata = {"backend": "DM_DeviceSDK 1.1.0", "device_index": index,
                         "channel": channel, "listen_only": False,
                         "application_sends_can_frames": False,
                         "sdk": str(Path(sdk).resolve()), "device_timestamp_unit": "unverified"}

    def start(self, submit):
        try:
            self.lib.dmcan_context_create(C.byref(self.ctx))
            if not self.ctx:
                raise RuntimeError("创建 SDK 上下文失败")
            count = self.lib.dmcan_find_devices(self.ctx)
            if not 0 <= self.index < count:
                raise RuntimeError(f"识别到 {count} 个达妙设备，当前编号 {self.index} 不存在。")
            if not self.lib.dmcan_device_get(self.ctx, C.byref(self.device), self.index):
                raise RuntimeError("获取设备失败")
            if not self.lib.dmcan_device_open(self.device):
                raise RuntimeError("打开设备失败：检查 USB 权限或是否被其他程序占用。")
            self.opened = True

            def receive(_device, pointer, direction):
                if self.closing:
                    return
                try:
                    frame = decode_sdk(pointer.contents, direction)
                    if frame.channel == self.channel:
                        submit(frame)
                except Exception as exc:
                    self.callback_errors += 1
                    self.failure = f"SDK 回调异常：{exc}"

            self.recv_callback = Callback(lambda d, p: receive(d, p, "RX"))
            self.err_callback = Callback(lambda d, p: receive(d, p, "ERR"))
            self.lib.dmcan_device_hook_recv_callback(self.device, self.recv_callback)
            self.lib.dmcan_device_hook_err_callback(self.device, self.err_callback)
            if not self.lib.dmcan_device_enable_channel(self.device, self.channel):
                raise RuntimeError("启用通道失败；单通道型号请选择 CH0。")
            self.enabled = True
            info = BaudInfo(self.channel, False, self.bitrate, self.bitrate, 0.75, 0.75)
            if not self.lib.dmcan_device_set_channel_baudrate(self.device, self.channel, info):
                raise RuntimeError("设置经典 CAN 波特率失败；检查设备固件与 SDK 兼容性。")
            actual = BaudInfo()
            if not self.lib.dmcan_device_get_channel_baudrate(self.device, self.channel, C.byref(actual)):
                raise RuntimeError("无法读回通道配置，未确认波特率。")
            if actual.can_baudrate != self.bitrate or actual.canfd:
                raise RuntimeError(f"配置读回不符：{actual.can_baudrate} bit/s，CAN FD={actual.canfd}")
            self.metadata["configured_bitrate"] = actual.can_baudrate
        except Exception:
            self.close()
            raise

    def close(self):
        self.closing = True
        if self.opened:
            if self.enabled:
                self.lib.dmcan_device_disable_channel(self.device, self.channel)
            self.lib.dmcan_device_close(self.device)
            self.opened = self.enabled = False
        if self.ctx:
            self.lib.dmcan_context_destroy(self.ctx)
            self.ctx = C.c_void_p()
        # Keep callbacks alive until context destruction has joined SDK threads.

    def send(self, can_id, data, extended=False):
        if len(data) > 8 or not 0 <= can_id <= (0x1fffffff if extended else 0x7ff):
            raise ValueError('Invalid classical CAN frame')
        function = self.lib.dmcan_device_send_can
        function.argtypes = [C.c_void_p, C.c_uint8, C.c_uint32, C.c_bool, C.c_bool,
                             C.c_bool, C.c_bool, C.c_uint8, C.POINTER(C.c_uint8)]
        function.restype = C.c_bool
        payload = (C.c_uint8 * 8).from_buffer_copy(bytes(data).ljust(8, b'\0'))
        if not function(self.device, self.channel, can_id, False, extended, False, False, len(data), payload):
            raise OSError('SDK CAN send failed')
        self.metadata['application_sends_can_frames'] = True


def scan_native(sdk=DEFAULT_SDK):
    lib = load_sdk(sdk)
    ctx = C.c_void_p()
    lib.dmcan_context_create(C.byref(ctx))
    if not ctx:
        raise RuntimeError("创建 SDK 上下文失败")
    try:
        count = lib.dmcan_find_devices(ctx)
        lib.dmcan_show_all_devices(ctx)
        return count
    finally:
        lib.dmcan_context_destroy(ctx)


class SocketBackend:
    def __init__(self, interface="can0"):
        self.interface = interface
        self.stop = threading.Event()
        self.failure = None
        self.kernel_dropped = 0
        self.metadata = {"backend": "SocketCAN", "interface": interface,
                         "application_sends_can_frames": False,
                         "listen_only": "configured externally"}

    def start(self, submit):
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            self.sock.setsockopt(socket.SOL_SOCKET, 40, 1)  # Linux SO_RXQ_OVFL
            self.sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_ERR_FILTER,
                                 struct.pack("=I", 0x1FFFFFFF))
            self.sock.settimeout(0.1)
            self.sock.bind((self.interface,))
        except Exception:
            self.sock.close()
            raise

        def worker():
            try:
                while not self.stop.is_set():
                    try:
                        data, ancillary, _flags, _address = self.sock.recvmsg(72, 64)
                    except socket.timeout:
                        continue
                    host_ns, wall_ns = time.monotonic_ns(), time.time_ns()
                    for level, kind, payload in ancillary:
                        if level == socket.SOL_SOCKET and kind == 40:
                            self.kernel_dropped = struct.unpack("=I", payload[:4])[0]
                    if len(data) != 16:
                        continue
                    can_id, dlc, len8_dlc, payload = struct.unpack("=IB2xB8s", data)
                    remote, error = bool(can_id & 0x40000000), bool(can_id & 0x20000000)
                    wire_dlc = len8_dlc if dlc == 8 and 9 <= len8_dlc <= 15 else min(dlc, 8)
                    submit(Frame(host_ns, wall_ns, can_id & 0x1FFFFFFF,
                                 b"" if remote else payload[:min(dlc, 8)],
                                 extended=bool(can_id & 0x80000000), remote=remote,
                                 direction="ERR" if error else "RX", dlc=wire_dlc))
            except Exception as exc:
                if not self.stop.is_set():
                    self.failure = f"SocketCAN 接收失败：{exc}"

        self.thread = threading.Thread(target=worker, name="socketcan-rx", daemon=True)
        self.thread.start()

    def send(self, can_id, data, extended=False):
        if len(data) > 8 or not 0 <= can_id <= (0x1fffffff if extended else 0x7ff):
            raise ValueError('Invalid classical CAN frame')
        packet = struct.pack('=IB3x8s', can_id | (0x80000000 if extended else 0), len(data), data)
        if self.sock.send(packet) != len(packet):
            raise OSError('Incomplete SocketCAN write')
        self.metadata['application_sends_can_frames'] = True

    def close(self):
        self.stop.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=2)
        if hasattr(self, "sock"):
            self.sock.close()


class DemoBackend:
    def __init__(self):
        self.stop = threading.Event()
        self.failure = None
        self.metadata = {"backend": "simulation", "synthetic": True,
                         "application_sends_can_frames": False}

    def start(self, submit):
        def worker():
            start = time.monotonic_ns()
            deadline = start
            count = 0
            motors = tuple(MOTORS)
            while not self.stop.is_set():
                elapsed = (deadline - start) / 1e9
                batch = int(elapsed) % 12 >= 6
                ids = motors if batch else (motors[count % 5],)
                for i, motor in enumerate(ids):
                    t = deadline + i * 220000
                    submit(Frame(t, time.time_ns(), motor, bytes.fromhex("80 00 80 00 00 00 08 00"),
                                 device_timestamp=t // 1000))
                    payload = bytes((0x10 | motor, 0x80, count % 256, 0x80, 0, 0, 35, 34))
                    submit(Frame(t + 130000, time.time_ns(), 0x10, payload,
                                 device_timestamp=(t + 130000) // 1000))
                count += 1
                deadline += 2000000
                self.stop.wait(max(0, (deadline - time.monotonic_ns()) / 1e9))

        self.thread = threading.Thread(target=worker, name="can-demo", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=2)
