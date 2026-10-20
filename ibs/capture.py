import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from .samples import PERF_FIELDS, parse_stream


def check():
    pmu = Path("/sys/bus/event_source/devices/ibs_op")
    paranoid = Path("/proc/sys/kernel/perf_event_paranoid")
    return dict(kernel=platform.release(), machine=platform.machine(),
                perf=shutil.which("perf"), ibs_op=pmu.is_dir(),
                perf_event_paranoid=paranoid.read_text().strip() if paranoid.exists() else None,
                uid=os.geteuid(),
                note="PMU presence does not prove permission to record; PA requires elevated privileges.")


def build_command(args, data):
    command = list(args.command)
    if command[:1] == ["--"]:
        command.pop(0)
    modes = bool(command) + bool(args.pid) + bool(args.cgroup) + bool(args.system)
    if modes != 1:
        raise ValueError("choose exactly one: -- COMMAND, --pid, --cgroup, --system")
    if command and args.duration is not None:
        raise ValueError("command mode ends with the command; use timeout in the command to limit duration")
    if not command and (args.duration is None or args.duration <= 0):
        raise ValueError("--pid/--cgroup/--system require positive --duration seconds")
    if not args.event.startswith("ibs_op/"):
        raise ValueError("--event must use the ibs_op PMU")
    if args.cpus and not (args.cgroup or args.system):
        raise ValueError("--cpus is supported only with --cgroup or --system")
    cmd = ["perf", "record", "--no-buildid-cache", "-o", str(data),
           "-e", args.event, "-d", "--phys-data", "--sample-cpu",
           "--timestamp", "--clockid", "mono"]
    cmd += ["-c", str(args.period)] if args.period else ["-F", str(args.freq)]
    if args.pid:
        if any(not x.isdigit() or int(x) < 1 for x in args.pid.split(",")):
            raise ValueError("--pid must be a comma-separated list of positive PIDs")
        cmd += ["-p", args.pid]
    if args.cgroup:
        root = Path("/sys/fs/cgroup").resolve()
        group = (root / args.cgroup.lstrip("/")).resolve()
        if not group.is_relative_to(root) or not (group / "cgroup.procs").is_file():
            raise ValueError("--cgroup must name an existing group relative to /sys/fs/cgroup")
        cmd += ["-a", "-G", str(group.relative_to(root)), "--all-cgroups"]
    if args.system:
        cmd += ["-a"]
    if args.cpus:
        cmd += ["-C", args.cpus]
    return cmd + ["--"] + (command or ["sleep", str(args.duration)])


def extract(data, csv_path, *, raw=None):
    """Stream perf script directly into CSV; publish only on successful parse/exit."""
    data, csv_path = Path(data), Path(csv_path)
    if not data.is_file():
        raise ValueError(f"perf data not found: {data}")
    if csv_path.exists() or (raw and Path(raw).exists()):
        raise ValueError("refusing to overwrite existing CSV/raw text")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    partial = csv_path.with_name(csv_path.name + ".partial")
    cmd = ["perf", "script", "-f", "--ns", "-i", str(data), "-F", PERF_FIELDS]
    env = dict(os.environ, LC_ALL="C", DEBUGINFOD_URLS="")
    # stderr stays visible and cannot deadlock behind a full pipe.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, env=env)
    raw_file = None
    try:
        if raw:
            raw_file = open(raw, "x")
        def lines():
            for line in proc.stdout:
                if raw_file:
                    raw_file.write(line)
                yield line
        with partial.open("x", newline="") as dest:
            stats = parse_stream(lines(), dest)
        if proc.wait() != 0:
            raise RuntimeError("perf script failed (see stderr)")
        partial.rename(csv_path)
        return stats
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if raw_file:
            raw_file.close()


def record(args):
    out = Path(args.out).resolve()
    cmd = build_command(args, out / "perf.data")
    # Captures contain addresses, command arguments and workload output.
    # Restrict the new directory even when the caller's umask is permissive.
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    metadata = dict(host=check(), command=cmd, cwd=os.getcwd(),
                    started_unix=time.time(), clock="CLOCK_MONOTONIC",
                    effective_uid=os.geteuid(), status="recording")
    meta_path = out / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print("Recording:", " ".join(cmd), flush=True)
    try:
        with (out / "record.log").open("w") as log:
            result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                    env=dict(os.environ, LC_ALL="C", DEBUGINFOD_URLS=""))
        metadata.update(returncode=result.returncode, finished_unix=time.time())
        if result.returncode:
            raise RuntimeError(f"perf/workload failed ({result.returncode}); see {out / 'record.log'}")
        metadata["parse"] = extract(out / "perf.data", out / "samples.csv",
                                    raw=out / "samples.txt" if args.keep_raw else None)
        if not args.no_plot:
            from .plotting import plot
            plot(out / "samples.csv", out / "plots", focus_comm=args.focus_comm,
                 block_bytes=args.block_bytes, max_points=args.max_points)
        metadata["status"] = "complete"
    except BaseException as exc:
        metadata.update(status="failed", error=str(exc))
        raise
    finally:
        meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata
