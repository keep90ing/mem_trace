"""Two-pass plotting: bounded sample memory, exact integer address ranks."""
import json
import math
from pathlib import Path

from .samples import read_csv


def selected(row, pids):
    return not pids or row["pid"] in pids


def bucket(row, key, block_bytes, include_kernel):
    value = row[key]
    # x86 lower canonical half (also admits LA57 userspace addresses).
    if value == 0 or (key == "va" and not include_kernel and value >= 1 << 63):
        return None
    return value // block_bytes * block_bytes


def collect(path, *, block_bytes=4194304, max_points=400000,
            focus_comm=None, pids=(), include_kernel=False):
    if block_bytes <= 0 or max_points <= 0:
        raise ValueError("block_bytes and max_points must be positive")
    pids = set(pids)
    counts = {(key, focus): 0 for key in ("va", "pa") for focus in (False, True)}
    blocks = {"va": set(), "pa": set()}
    start = end = None
    samples = 0
    pid_set = set()
    for row in read_csv(path):
        if not selected(row, pids):
            continue
        samples += 1
        pid_set.add(row["pid"])
        t = row["time_ns"]
        start = t if start is None else min(start, t)
        end = t if end is None else max(end, t)
        focus = focus_comm is None or focus_comm.casefold() in row["comm"].casefold()
        for key in blocks:
            b = bucket(row, key, block_bytes, include_kernel)
            if b is not None:
                blocks[key].add(b)
                counts[key, focus] += 1
    if samples == 0:
        raise ValueError("no samples match the CSV/PID selection")
    ranks = {key: {b: i for i, b in enumerate(sorted(values))}
             for key, values in blocks.items()}
    # Exact, evenly spaced sample indices, including endpoints.
    targets = {}
    for group, n in counts.items():
        k = min(n, max_points)
        targets[group] = {i * (n - 1) // (k - 1) for i in range(k)} if k > 1 else {0}
    points = {group: [] for group in counts}
    seen = dict.fromkeys(counts, 0)
    for row in read_csv(path):
        if not selected(row, pids):
            continue
        focus = focus_comm is None or focus_comm.casefold() in row["comm"].casefold()
        for key in blocks:
            b = bucket(row, key, block_bytes, include_kernel)
            if b is None:
                continue
            group = (key, focus)
            if seen[group] in targets[group]:
                points[group].append(((row["time_ns"] - start) / 1e9, ranks[key][b]))
            seen[group] += 1
    return dict(samples=samples, start_ns=start, span_sec=(end-start)/1e9,
                pids=sorted(pid_set), counts=counts, ranks=ranks, points=points)


def plot(path, out, **options):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data = collect(path, **options)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    block = options.get("block_bytes", 4194304)
    focus = options.get("focus_comm") or "all selected samples"
    summary = {k: data[k] for k in ("samples", "start_ns", "span_sec", "pids")}
    summary.update(block_bytes=block, focus_comm=focus, series={})
    for key in ("va", "pa"):
        fig, ax = plt.subplots(figsize=(20, 12))
        for is_focus, color in ((False, "blue"), (True, "red")):
            points = data["points"][key, is_focus]
            n = data["counts"][key, is_focus]
            label = focus if is_focus else "others"
            summary["series"][f"{key}_{'focus' if is_focus else 'others'}"] = dict(
                valid_samples=n, plotted_points=len(points))
            if points:
                x, y = zip(*points)
                ax.scatter(x, y, s=0.6, c=color, alpha=0.55 if is_focus else 0.3,
                           linewidths=0, label=f"{label} (n={n:,})")
        values = list(data["ranks"][key])
        if values:
            step = max(1, math.ceil(len(values) / 16))
            ticks = list(range(0, len(values), step))
            ax.set_yticks(ticks, [hex(values[i]) for i in ticks])
            ax.set_ylim(-1, len(values))
            ax.legend(loc="upper right", markerscale=4)
        else:
            ax.text(0.5, 0.5, "No nonzero usable addresses", ha="center", transform=ax.transAxes)
        ax.set_xlim(0, max(data["span_sec"], 0.001))
        ax.set_xlabel("Time since first selected sample (sec)")
        ax.set_ylabel(f"Address block rank (block size {block:,} bytes; gaps removed)")
        ax.set_title(f"Data {'Virtual' if key == 'va' else 'Physical'} Address Timeline")
        note = f"red = {focus}\nblue = others\nunique blocks = {len(values):,}"
        if key == "va" and len(data["pids"]) > 1:
            note += "\nMultiple PIDs: equal VA does not imply shared memory"
        ax.text(0.005, 0.995, note, transform=ax.transAxes, va="top", fontsize=9,
                bbox=dict(facecolor="white", alpha=0.85))
        ax.grid(True, alpha=0.2)
        fig.tight_layout()
        fig.savefig(out / f"data_{key}_timeline.jpg", dpi=110)
        plt.close(fig)
        summary[f"{key}_blocks"] = len(values)
    (out / "plot_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
