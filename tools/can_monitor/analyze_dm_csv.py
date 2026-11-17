"""Offline DM Tool CSV load estimate. No hardware access or firmware changes."""
import argparse
from collections import Counter
import csv
import json
from .core import classic_frame_bits


def analyze(path, bitrate=1_000_000):
    counts, wire, timestamps = Counter(), Counter(), Counter()
    first = last = None
    previous_sequence = None
    sequence_gaps = 0
    offset = 0
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row["帧格式"], row["帧类型"], row["CAN类型"], row["状态"]) != (
                    "标准帧", "数据帧", "CAN", "接收成功"):
                raise ValueError("Only successful classical standard data frames supported")
            identifier = int(row["ID"], 16)
            payload = bytes.fromhex(row["数据内容"])
            if not 0 <= identifier <= 0x7ff or len(payload) != int(row["数据长度"]) or len(payload) > 8:
                raise ValueError("Invalid standard identifier or DLC")
            hour, minute, second = row["时间戳"].split(":")
            stamp = int(hour)*3600 + int(minute)*60 + float(second) + offset
            if last is not None and stamp < last - 43200:
                offset += 86400
                stamp += 86400
            if last is not None and stamp < last:
                raise ValueError("Nonmonotonic timestamps")
            first = stamp if first is None else first
            last = stamp
            sequence = int(row["序号"])
            sequence_gaps += previous_sequence is not None and sequence != previous_sequence + 1
            previous_sequence = sequence
            timestamps[stamp] += 1
            counts[identifier] += 1
            wire[identifier] += classic_frame_bits(identifier, payload, dlc=len(payload))
    if first is None or last <= first or bitrate <= 0:
        raise ValueError("Need positive capture duration and bitrate")
    duration = last - first
    return {
        "frames": sum(counts.values()), "duration_s": duration,
        "bitrate": bitrate, "fps": sum(counts.values())/duration,
        "sequence_gaps": sequence_gaps, "timestamp_groups": len(timestamps),
        "max_frames_same_timestamp": max(timestamps.values()),
        "load_percent": sum(wire.values())/duration/bitrate*100,
        "wire_bits": sum(wire.values()),
        "limitations": "Successful observed frames only; includes CRC15/stuffing/IFS; excludes errors/retries. Batched host timestamps do not establish frame latency or short-window peaks.",
        "ids": {f"0x{i:03X}": {"frames": counts[i], "fps": counts[i]/duration,
                                  "load_percent": wire[i]/duration/bitrate*100}
                for i in sorted(counts)},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture")
    parser.add_argument("--bitrate", type=int, default=1_000_000)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture, args.bitrate), ensure_ascii=False, indent=2))
