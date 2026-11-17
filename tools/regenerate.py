#!/usr/bin/env python3
"""Regenerate the MCU project using an installed STM32CubeMX (keeps USER CODE)."""
import argparse
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cubemx", default=os.environ.get("STM32CUBEMX", str(Path.home() / "STM32CubeMX/STM32CubeMX")),
                    help="CubeMX launcher or executable JAR path; alternatively set STM32CUBEMX")
args = parser.parse_args()
mx = Path(args.cubemx).expanduser().resolve()
if not mx.is_file():
    parser.error(f"CubeMX not found: {mx}")
java = mx.parent / "jre/bin" / ("java.exe" if os.name == "nt" else "java")
if not java.is_file():
    java = shutil.which("java")
    if not java:
        parser.error("Java not found")
project = root / "uart_to_can"
with tempfile.TemporaryDirectory(prefix="shark-cubemx-") as temp:
    script = Path(temp) / "generate.mx"
    script.write_text(
        f'config load "{(project / "uart_to_can.ioc").as_posix()}"\n'
        'project name uart_to_can\nproject toolchain CMake\n'
        'project generate\nconfig save\nexit\n', encoding="utf-8")
    result = subprocess.run([str(java), "-jar", str(mx), "-q", str(script)],
                            cwd=mx.parent, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    print(result.stdout, end="")
    if re.search(r"^KO$", result.stdout, flags=re.M) or "ProjectBuilder:" not in result.stdout:
        raise SystemExit("CubeMX did not complete generation; inspect the output above.")
# Reserve two 2 KiB pages inside the first 64 KiB, including on the observed
# dual-bank die. Keep configuration outside every ELF download/erase range.
linker = project / "STM32G431xx_FLASH.ld"
source = linker.read_text()
source, count = re.subn(r'(FLASH \(rx\)\s*: ORIGIN = 0x8000000, LENGTH = )(?:128K|60K)', r'\g<1>60K', source)
if count != 1:
    raise SystemExit("Cannot reserve ID storage: unrecognized Flash linker layout")
linker.write_text(source)
print("CubeMX exited; Flash capped at 60 KiB, ID journal at 0x0800F000..0x0800FFFF.")
print("Build from the workspace root: cmake --preset Debug && cmake --build --preset Debug")
