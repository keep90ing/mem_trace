"""Streaming parser for the project's explicit perf script field contract."""
import csv
import re
from decimal import Decimal

FIELDS = ["time_sec", "comm", "pid", "tid", "cpu", "va", "pa", "ip", "event"]
PERF_FIELDS = "comm,pid,tid,cpu,time,event,ip,addr,phys_addr"
# perf prints addr BEFORE ip regardless of the order in -F. Symbols/DSOs
# from the old project's export are optional; physical address is last.
HEADER = re.compile(
    r"^\s*(?P<comm>.+?)\s+(?P<pid>-?\d+)/(?P<tid>-?\d+)\s+"
    r"\[(?P<cpu>\d+)\]\s+(?P<time>\d+\.\d+):\s+"
    r"(?P<event>\S+):\s+(?P<body>.+?)\s*$"
)
HEX = re.compile(r"(?:0[xX])?[0-9a-fA-F]{1,16}\Z")


def address(token):
    if not HEX.fullmatch(token):
        raise ValueError(f"invalid 64-bit address: {token!r}")
    return int(token, 16)


def parse_line(line):
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    m = HEADER.fullmatch(line.rstrip("\n"))
    if not m:
        raise ValueError("unrecognized perf sample header")
    v = m.groupdict()
    if not v["event"].startswith("ibs_op/"):
        raise ValueError(f"expected ibs_op event, got {v['event']!r}")
    body = v["body"].split()
    if len(body) < 3:
        raise ValueError("sample missing addr, ip or phys_addr")
    return dict(time_sec=format(Decimal(v["time"]), ".9f"),
                comm=v["comm"], pid=v["pid"], tid=v["tid"],
                cpu=str(int(v["cpu"])), va=hex(address(body[0])),
                pa=hex(address(body[-1])), ip=hex(address(body[1])),
                event=v["event"])


def parse_stream(source, destination, *, skip_invalid=False):
    writer = csv.DictWriter(destination, fieldnames=FIELDS)
    writer.writeheader()
    stats = dict(lines=0, samples=0, skipped=0, zero_va=0, zero_pa=0)
    for number, line in enumerate(source, 1):
        stats["lines"] += 1
        try:
            row = parse_line(line)
        except ValueError as exc:
            if not skip_invalid:
                raise ValueError(f"line {number}: {exc}") from exc
            stats["skipped"] += 1
            continue
        if row is not None:
            writer.writerow(row)
            stats["samples"] += 1
            stats["zero_va"] += row["va"] == "0x0"
            stats["zero_pa"] += row["pa"] == "0x0"
    if not stats["samples"]:
        raise ValueError("no IBS samples parsed")
    return stats


def read_csv(path):
    """Also accepts the old project's seven-column CSV without ip/event."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        required = set(FIELDS[:7])
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV requires columns: {sorted(required)}")
        for number, row in enumerate(reader, 2):
            try:
                row["time_ns"] = int(Decimal(row["time_sec"]) * 1_000_000_000)
                for key in ("pid", "tid", "cpu"):
                    row[key] = int(row[key])
                for key in ("va", "pa"):
                    row[key] = address(row[key])
            except (ValueError, TypeError, ArithmeticError) as exc:
                raise ValueError(f"invalid CSV row {number}: {exc}") from exc
            yield row
