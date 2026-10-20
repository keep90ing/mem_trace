"""Compare samples with independent workload phase and pagemap observations."""
import json
from collections import Counter

from .samples import read_csv


def validate(csv_path, manifest, *, min_samples=20, require_pa=False, min_coverage=0.25):
    if not 0 <= min_coverage <= 1:
        raise ValueError("min_coverage must be between 0 and 1")
    with open(manifest) as source:
        records = [json.loads(line) for line in source if line.strip()]
    regions = {r["name"]: r for r in records if r["type"] == "region"}
    phases = [r for r in records if r["type"] == "phase"]
    if not regions or not phases:
        raise ValueError("manifest has no regions/phases; workload may not have finished")
    pages = {(r["region"], r["when"], r["index"]): r["pa"]
             for r in records if r["type"] == "page"}
    counts = [Counter() for _ in phases]
    for row in read_csv(csv_path):
        for i, phase in enumerate(phases):
            if row["pid"] != phase["pid"] or not phase["start_ns"] <= row["time_ns"] < phase["end_ns"]:
                continue
            active = regions[phase["region"]]
            base, size = int(active["va"], 16), active["size"]
            va, pa = row["va"], row["pa"]
            if base <= va < base + size:
                offset = va - base
                if not counts[i]["hits"]:
                    counts[i]["min_offset"] = offset
                    counts[i]["max_offset"] = offset
                counts[i]["min_offset"] = min(counts[i]["min_offset"], offset)
                counts[i]["max_offset"] = max(counts[i]["max_offset"], offset)
                counts[i]["hits"] += 1
                if va >= base + phase["active_bytes"]:
                    counts[i]["outside_active"] += 1
                if pa:
                    counts[i]["nonzero_pa"] += 1
                    page_size = active["page_size"]
                    if va % page_size != pa % page_size:
                        counts[i]["offset_mismatch"] += 1
                    index = (va-base) // page_size
                    before = pages.get((phase["region"], "before", index))
                    after = pages.get((phase["region"], "after", index))
                    if before and before == after:
                        counts[i]["pa_checked"] += 1
                        if pa != int(before, 16) + (va-base) % page_size:
                            counts[i]["pa_mismatch"] += 1
            else:
                for other in regions.values():
                    lo = int(other["va"], 16)
                    if lo <= va < lo + other["size"]:
                        counts[i]["wrong_region"] += 1
                        break
                # Stacks, libc, kernel and manifest I/O are outside the test arrays.
                counts[i]["other"] += 1
            break
    failures = []
    reports = []
    for phase, count in zip(phases, counts):
        name = phase["name"]
        if count["hits"] < min_samples:
            failures.append(f"{name}: only {count['hits']} array samples, need {min_samples}")
        coverage = (count["max_offset"] - count["min_offset"] + 1) / phase["active_bytes"] if count["hits"] else 0
        if coverage < min_coverage:
            failures.append(f"{name}: sampled address span {coverage:.3f} < {min_coverage}")
        for key in ("outside_active", "wrong_region", "offset_mismatch", "pa_mismatch"):
            if count[key]:
                failures.append(f"{name}: {key}={count[key]}")
        if require_pa and count["pa_checked"] < min_samples:
            failures.append(f"{name}: insufficient stable pagemap PA comparisons ({count['pa_checked']})")
        reports.append(dict(phase=name, sampled_span_fraction=coverage, **count))
    pa_checked = sum(c["pa_checked"] for c in counts)
    return dict(passed=not failures, failures=failures, phases=reports,
                pa_validation="compared_with_pagemap" if pa_checked else "unavailable",
                caveat="Before/after pagemap snapshots cannot exclude migration away and back between snapshots.")
