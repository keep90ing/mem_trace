#!/usr/bin/env python3
"""Independently compare CSV with perf's time/addr/phys_addr-only export.

This verifies decoding and column order on a real recording without PMU
privileges. It does not verify the physical hardware or original cgroup filter.
"""
import argparse
import csv
import itertools
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path


def verify(data, csv_path):
    cmd = ["perf", "script", "-f", "--ns", "-i", str(data), "-F", "time,addr,phys_addr"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True,
                            env=dict(os.environ, LC_ALL="C", DEBUGINFOD_URLS=""))
    count = 0
    try:
        with open(csv_path, newline="") as source:
            reader = csv.DictReader(source)
            for number, pair in enumerate(itertools.zip_longest(proc.stdout, reader), 1):
                line, row = pair
                if line is None or row is None:
                    raise ValueError(f"sample count differs at row {number}")
                time, va, pa = line.split()
                expected = (Decimal(time.rstrip(":")), int(va, 16), int(pa, 16))
                actual = (Decimal(row["time_sec"]), int(row["va"], 16), int(row["pa"], 16))
                if expected != actual:
                    raise ValueError(f"sample {number} mismatch: {expected} != {actual}")
                count += 1
        if proc.wait() != 0 or count == 0:
            raise ValueError("perf script failed or recording is empty")
        return dict(passed=True, samples_compared=count,
                    fields=["time_sec", "va", "pa"],
                    method="independent perf script -F time,addr,phys_addr",
                    hardware_validation=False)
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.data, args.csv)
    except (OSError, ValueError) as exc:
        report = dict(passed=False, error=str(exc))
    text = json.dumps(report, indent=2) + "\n"
    print(text, end="")
    if args.out:
        with args.out.open("x") as f:
            f.write(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
