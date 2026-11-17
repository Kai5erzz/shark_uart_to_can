#!/usr/bin/env python3
"""Bundle source, both host tools, verified firmware and validation evidence."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ('CMakeLists.txt', 'CMakePresets.json', 'README.md', '.gitignore')
SOURCE_DIRS = ('.vscode', 'cmake', 'openocd', 'uart_to_can', 'tools', 'tests', 'docs')
EXCLUDE = {'build', '__pycache__', '.git', '.venv', 'vendor', 'captures', '.pytest_cache'}
LOGS = ('uart2-raw-debug.log', 'uart2-raw-release.log', 'uart2-raw-flash.log',
        'uart2-raw-host-tests.log', 'uart2-raw-monitor-tests.log', 'uart2-raw-gui-final.log',
        'package-host-tests.log', 'package-monitor-tests.log',
        'package-clean-configure.log', 'package-clean-build.log',
        'package-clean-host-tests.log', 'package-clean-monitor-tests.log')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def collect():
    paths = {ROOT / name for name in SOURCE_FILES}
    for name in SOURCE_DIRS:
        for path in (ROOT / name).rglob('*'):
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDE for part in relative.parts) or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink():
                raise ValueError(f'Unexpected source symlink: {relative}')
            if path.is_file():
                paths.add(path)
    if not (ROOT / 'tools/can_monitor/app.py').is_file():
        raise ValueError('Missing bundled CAN Monitor source')
    manifest = json.loads((ROOT / 'docs/build_artifacts.json').read_text())
    for build in ('Debug', 'Release'):
        for kind in ('elf', 'hex', 'bin', 'map'):
            row = manifest[build][kind]
            path = ROOT / row['path']
            if not path.resolve().is_relative_to(ROOT):
                raise ValueError('Artifact path must stay inside the project')
            data = path.read_bytes()
            if len(data) != row['bytes'] or digest(data) != row['sha256']:
                raise ValueError(f'Artifact manifest is stale: {row["path"]}')
            paths.add(path)
    for name in LOGS:
        path = ROOT / 'build' / name
        if path.is_file():
            paths.add(path)
    for path in (ROOT / 'build/performance').rglob('*'):
        if path.is_file() and not path.is_symlink() and path.suffix in ('.json', '.csv', '.txt'):
            paths.add(path)
    return sorted(paths), manifest


def main():
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--name', default=f'shark_uart_to_can_dev_{now:%Y%m%d}')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', args.name):
        parser.error('Name must contain only letters, digits, hyphens or underscores')
    try:
        paths, artifacts = collect()
        payload = []
        records = []
        for path in paths:
            relative, data = path.relative_to(ROOT).as_posix(), path.read_bytes()
            payload.append((path, relative, data))
            records.append(dict(path=relative, bytes=len(data), sha256=digest(data)))
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    metadata = dict(
        name=args.name, created_at=now.isoformat(), status='development_not_release_qualified',
        hardware='STM32G431CBT6 / TJA1044 / HSE 32 MHz',
        firmware=artifacts,
        current_board_id_observed=5, factory_default_id=1, flash_id_journal_included=False,
        status_note='The runtime USART2 baud feature in this package has not been flashed; previous image internal 16 KiB loopback passed and external raw duplex failed; termination correction not retested.',
        entry_points=['docs/user-guide.md', 'docs/protocol.md', 'docs/package-notes.md'],
        omitted=['build caches and objects', 'Flash backups', 'complete Cube SDK archives',
                 'CAN Monitor vendor binaries and local runtime'],
        files=records)
    package_data = (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode()
    checksums = ''.join(f'{row["sha256"]}  {row["path"]}\n' for row in records)
    checksums += f'{digest(package_data)}  PACKAGE.json\n'
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / f'{args.name}.zip'
    with tempfile.TemporaryDirectory(prefix='shark-package-', dir=args.output) as temp:
        temporary = Path(temp) / 'package.zip'
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, relative, data in payload:
                info = zipfile.ZipInfo.from_file(path, f'{args.name}/{relative}')
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            for name, data in (('PACKAGE.json', package_data), ('SHA256SUMS', checksums.encode())):
                info = zipfile.ZipInfo(f'{args.name}/{name}', now.timetuple()[:6])
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(destination)
    checksum = digest(destination.read_bytes())
    destination.with_suffix('.zip.sha256').write_text(f'{checksum}  {destination.name}\n')
    print(json.dumps(dict(archive=str(destination), bytes=destination.stat().st_size,
                          files=len(records) + 2, sha256=checksum), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
