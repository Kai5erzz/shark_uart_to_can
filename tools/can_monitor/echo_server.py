"""Headless CAN Monitor echo responder; same backend and callback as the GUI."""
import argparse
import json
from pathlib import Path
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = 'tools.can_monitor'

from .serial_backend import SerialBackend
from .backends import SocketBackend, NativeBackend
from .echo import EchoResponder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='Damiao CDC serial port')
    parser.add_argument('--socketcan', help='Configured SocketCAN interface')
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--bitrate', type=int, default=1000000)
    parser.add_argument('--request-id', type=lambda s: int(s, 16), default=0x6e0)
    parser.add_argument('--reply-id', type=lambda s: int(s, 16), default=0x6e1)
    parser.add_argument('--seconds', type=float, default=0, help='0 waits until Ctrl+C')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if sum((bool(args.port), bool(args.socketcan), args.native)) != 1 or args.seconds < 0:
        parser.error('Select exactly one backend; seconds must be non-negative')
    backend = (SerialBackend(args.port, args.bitrate) if args.port else
               SocketBackend(args.socketcan) if args.socketcan else NativeBackend(bitrate=args.bitrate))
    echo = EchoResponder()
    echo.configure(True, args.request_id, args.reply_id)
    failure = None
    try:
        backend.start(lambda frame: echo.handle(frame, backend.send))
        print('READY CAN Monitor echo', flush=True)
        end = time.monotonic() + args.seconds if args.seconds else float('inf')
        while time.monotonic() < end:
            if backend.failure:
                raise RuntimeError(backend.failure)
            time.sleep(.05)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        failure = str(exc)
    finally:
        backend.close()
    result = dict(echo=echo.snapshot(), backend=backend.metadata, failure=failure)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return int(bool(failure or result['echo']['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
