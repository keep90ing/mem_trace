#!/usr/bin/env python3
"""Generic AMD IBS memory access profiler. See README.md for examples."""
import argparse
import json
import math
import sys
from pathlib import Path

from ibs.capture import check, extract, record
from ibs.samples import parse_stream


def positive(value):
    result = int(value, 0)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def duration(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be positive finite seconds")
    return result


def plot_options(parser):
    parser.add_argument("--focus-comm", help="case-insensitive literal substring; default: all red")
    parser.add_argument("--block-bytes", type=positive, default=4 * 1024 * 1024)
    parser.add_argument("--max-points", type=positive, default=400000, help="per address/color series")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("check", help="report host support, without changing system settings")
    p = commands.add_parser("record", help="record, extract and plot into a NEW directory")
    p.add_argument("--out", required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--pid", help="existing PID or comma-separated PIDs")
    mode.add_argument("--cgroup", help="cgroup v2 path relative to /sys/fs/cgroup")
    mode.add_argument("--system", action="store_true")
    p.add_argument("--duration", type=duration)
    p.add_argument("--cpus", help="CPU list, for system/cgroup mode only")
    p.add_argument("--event", default="ibs_op//")
    rate = p.add_mutually_exclusive_group()
    rate.add_argument("--freq", type=positive, default=2000)
    rate.add_argument("--period", type=positive)
    p.add_argument("--keep-raw", action="store_true")
    p.add_argument("--no-plot", action="store_true")
    plot_options(p)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = commands.add_parser("extract", help="perf.data -> CSV")
    p.add_argument("data")
    p.add_argument("--out", required=True)
    p.add_argument("--raw", help="optional raw perf script text")
    p = commands.add_parser("parse", help="existing perf script text -> CSV")
    p.add_argument("input", help="file, or - for stdin")
    p.add_argument("--out", required=True)
    p.add_argument("--skip-invalid", action="store_true", help="explicitly allow malformed lines; reports count")
    p = commands.add_parser("plot", help="CSV -> VA/PA JPEG timelines")
    p.add_argument("csv")
    p.add_argument("--out", required=True)
    plot_options(p)
    p.add_argument("--pid", type=positive, action="append", default=[])
    p.add_argument("--include-kernel", action="store_true")
    p = commands.add_parser("validate", help="compare CSV with memory_phases manifest")
    p.add_argument("csv")
    p.add_argument("manifest")
    p.add_argument("--min-samples", type=positive, default=20)
    p.add_argument("--require-pa", action="store_true")
    p.add_argument("--min-coverage", type=float, default=0.25,
                   help="minimum sampled VA span / active size per phase (default: .25)")
    p.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        if args.action == "check":
            result = check()
        elif args.action == "record":
            result = record(args)
        elif args.action == "extract":
            result = extract(args.data, args.out, raw=args.raw)
        elif args.action == "parse":
            out = Path(args.out)
            if out.exists():
                raise ValueError(f"refusing to overwrite {out}")
            out.parent.mkdir(parents=True, exist_ok=True)
            source = sys.stdin if args.input == "-" else open(args.input)
            partial = out.with_name(out.name + ".partial")
            try:
                with partial.open("x", newline="") as dest:
                    result = parse_stream(source, dest, skip_invalid=args.skip_invalid)
                partial.rename(out)
            finally:
                if source is not sys.stdin:
                    source.close()
        elif args.action == "plot":
            from ibs.plotting import plot
            result = plot(args.csv, args.out, focus_comm=args.focus_comm,
                          block_bytes=args.block_bytes, max_points=args.max_points,
                          pids=args.pid, include_kernel=args.include_kernel)
        else:
            from ibs.validation import validate
            result = validate(args.csv, args.manifest, min_samples=args.min_samples,
                              require_pa=args.require_pa, min_coverage=args.min_coverage)
            if args.out:
                with open(args.out, "x") as f:
                    json.dump(result, f, indent=2)
                    f.write("\n")
            print(json.dumps(result, indent=2))
            return 0 if result["passed"] else 1
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
