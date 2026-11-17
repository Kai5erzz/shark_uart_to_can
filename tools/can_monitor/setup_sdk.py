#!/usr/bin/env python3
"""Install pinned official SDK and optional local libusb without root."""
import argparse
import base64
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REVISION = "36a8b3971dc35608701d128f353a32cad3ee6cdb"
SDK_SHA = "bec4ca4421f5366c90fa11cc34f2f683ed71a6372e15c265e7e4b17e720607dd"
SDK_BLOB = "52db55cd308002d9457e98518ac653d6d6de4937"
HEADER_SHA = "05941f0b792cdb706b45dd03b36e7b000feb45d533866bfab89ae14db60d1bb0"
USB_SHA = "24fd52178a466ef26d33966a4f5703d02dd320d7a57b8ae800e8283ace639ec2"


def download(url, json_blob=False):
    error = None
    for attempt in range(8):
        try:
            request = urllib.request.Request(url + ("&" if "?" in url else "?") + f"attempt={attempt}",
                                             headers={"User-Agent": "code914-can-monitor"})
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read()
            return base64.b64decode(json.loads(data)["content"]) if json_blob else data
        except Exception as exc:
            error = exc
    raise RuntimeError(f"下载失败：{url}: {error}")


def checked(data, digest):
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError("下载内容 SHA256 不符，未安装。")
    return data


def install(args):
    if platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("自动安装当前固定为 Linux x86_64；ARM 请从官方 SDK 选对应架构。")
    vendor = HERE / "vendor"
    vendor.mkdir(exist_ok=True)
    if args.cache:
        sdk = (args.cache / "libdm_device.so").read_bytes()
        header = (args.cache / "dmcan.h").read_bytes()
    else:
        api = "https://api.github.com/repos/dmBots/dm-device-sdk/git/blobs/"
        sdk = download(api + SDK_BLOB, True)
        header = download(api + "6c910ab663e9d3bb8ff6084c455aca93e6d0878e", True)
    (vendor / "libdm_device.so").write_bytes(checked(sdk, SDK_SHA))
    (vendor / "dmcan.h").write_bytes(checked(header, HEADER_SHA))
    print("官方 DM_DeviceSDK v1.1.0 已安装", flush=True)
    if args.local_libusb:
        archive = ((args.cache / "libusb-1.0.27.tar.gz").read_bytes() if args.cache else
                   download("https://api.github.com/repos/libusb/libusb/tarball/v1.0.27"))
        checked(archive, USB_SHA)
        for command in ("gcc", "make", "autoreconf", "libtoolize"):
            if not shutil.which(command):
                raise RuntimeError(f"构建本地 libusb 需要 {command}（build-essential/autoconf/automake/libtool）")
        with tempfile.TemporaryDirectory(prefix="can-libusb-") as temp:
            archive_path = Path(temp) / "source.tar.gz"
            archive_path.write_bytes(archive)
            with tarfile.open(archive_path) as tar:
                regular = []
                for member in tar.getmembers():
                    path = Path(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError("归档路径异常")
                    if member.isfile() or member.isdir():
                        regular.append(member)
                tar.extractall(temp, members=regular)
            source = next(path for path in Path(temp).iterdir() if path.is_dir())
            commands = [["./bootstrap.sh"], ["./configure", f"--prefix={vendor / 'runtime'}",
                         "--disable-static", "--disable-udev"], ["make", "-j4"], ["make", "install"]]
            for command in commands:
                subprocess.run(command, cwd=source, check=True, stdout=subprocess.DEVNULL)
            shutil.copyfile(source / "COPYING", vendor / "LIBUSB-COPYING")
            # Preserve the checked LGPL source archive with the local build.
            (vendor / "libusb-1.0.27-source.tar.gz").write_bytes(archive)
        print("本地 libusb 1.0.27 已安装；系统库未修改", flush=True)
    (vendor / "sdk_source.json").write_text(json.dumps({
        "repository": "https://github.com/dmBots/dm-device-sdk", "revision": REVISION,
        "path": "C&C++/lib/v1.1.0/linux/x86_64/libdm_device.so", "sha256": SDK_SHA,
        "redistribution": "SDK remains a separate official download; no license grant inferred",
        "libusb_source_sha256": USB_SHA if args.local_libusb else None}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-libusb", action="store_true", help="为旧 Ubuntu 构建本地 libusb 1.0.27")
    parser.add_argument("--cache", type=Path, help="已有的校验一致的 SDK/源码下载目录")
    install(parser.parse_args())
