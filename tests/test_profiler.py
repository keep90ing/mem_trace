import argparse
import contextlib
import csv
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ibs.samples import FIELDS, parse_line, parse_stream
from ibs.plotting import collect, plot
from ibs.capture import build_command, extract, record
from ibs.validation import validate


class ParserTests(unittest.TestCase):
    def test_legacy_format_and_spaces_in_comm(self):
        # Synthetic values retain perf's field order and high-address cases.
        line = " worker pool 123/456 [007] 12.345678: ibs_op//: ffff800000001018 ffffffff81001000 fn (some file.so) 12345018"
        r = parse_line(line)
        self.assertEqual(r["comm"], "worker pool")
        self.assertEqual(r["va"], "0xffff800000001018")
        self.assertEqual(r["ip"], "0xffffffff81001000")
        self.assertEqual(r["pa"], "0x12345018")
        self.assertEqual(r["cpu"], "7")

    def test_minimal_nanoseconds_and_zero(self):
        r = parse_line("x 1/2 [0] 123456.123456789: ibs_op/cnt_ctl=1/: 0 7fffabcd 0")
        self.assertEqual(r["time_sec"], "123456.123456789")
        self.assertEqual(r["va"], "0x0")
        self.assertEqual(r["pa"], "0x0")

    def test_unknown_tid(self):
        r = parse_line(" :-1 123/-1 [003] 12.345678901: ibs_op//: ffff800000001230 ffffffff81002000 12345230")
        self.assertEqual(r["tid"], "-1")

    def test_bad_fields_are_not_silently_zeroed(self):
        for line in ["bad line", "x 1/2 [0] 1.0: ibs_op//: xyz 123 456",
                     "x 1/2 [0] 1.0: ibs_op//: 123 456",
                     "x 1/2 [0] 1.0: cycles: 123 456 789",
                     "x 1/2 [0] 1.0: ibs_op//: 10000000000000000 123 456"]:
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_line(line)

    def test_skip_stats_and_empty_failure(self):
        stats = parse_stream(io.StringIO("# comment\n\nbad\nx 1/2 [0] 1.0: ibs_op//: 0 1 0\n"),
                             io.StringIO(), skip_invalid=True)
        self.assertEqual(stats, dict(lines=4, samples=1, skipped=1, zero_va=1, zero_pa=1))
        with self.assertRaisesRegex(ValueError, "no IBS"):
            parse_stream(io.StringIO("\n"), io.StringIO())


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def csv(self, rows, name="samples.csv"):
        p = self.path / name
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(dict(time_sec="1.0", comm="test", pid=1, tid=1, cpu=0,
                                va="0x1000", pa="0x2000", ip="0x9876", event="ibs_op//") | r)
        return p

    def test_rank_union_and_exact_uint64(self):
        p = self.csv([dict(comm="focus", va="0x1000"),
                      dict(comm="other", va="0x2000"),
                      dict(comm="focus", va="0xffffffffffffffff")])
        d = collect(p, focus_comm="focus", block_bytes=4096, include_kernel=True)
        self.assertEqual(d["ranks"]["va"], {0x1000: 0, 0x2000: 1, 0xfffffffffffff000: 2})
        self.assertEqual(d["points"]["va", False], [(0., 1)])
        self.assertEqual(collect(p)["counts"]["va", True], 2)

    def test_downsample_endpoints_time_origin_and_pid(self):
        p = self.csv([dict(time_sec=f"{20-i}.000000001", va=hex(0x1000+i*4096)) for i in range(10)]
                     + [dict(pid=99, time_sec="0.0")])
        d = collect(p, max_points=3, pids=[1], block_bytes=4096)
        self.assertEqual(d["start_ns"], 11000000001)
        self.assertEqual(d["points"]["va", True], [(9., 0), (5., 4), (0., 9)])
        with self.assertRaises(ValueError):
            collect(p, pids=[999])
        with self.assertRaises(ValueError):
            collect(p, max_points=0)

    def test_empty_pa_plot_and_old_csv(self):
        p = self.path / "old.csv"
        p.write_text("time_sec,comm,pid,tid,cpu,va,pa\n1.0,worker,1,1,0,0x1234,0x0\n")
        summary = plot(p, self.path / "plots")
        self.assertEqual(summary["pa_blocks"], 0)
        for key in ("va", "pa"):
            image = self.path / "plots" / f"data_{key}_timeline.jpg"
            self.assertEqual(image.read_bytes()[:2], b"\xff\xd8")
            self.assertGreater(image.stat().st_size, 10000)

    def manifest(self):
        records = [dict(type="region", name="A", va="0x1000", size=8192, page_size=4096),
                   dict(type="region", name="B", va="0x9000", size=4096, page_size=4096),
                   dict(type="phase", name="small", region="A", pid=1,
                        start_ns=1000000000, end_ns=2000000000, active_bytes=4096)]
        for when in ("before", "after"):
            records.append(dict(type="page", region="A", when=when, index=0, pa="0x5000"))
        p = self.path / "manifest.jsonl"
        p.write_text("".join(json.dumps(r)+"\n" for r in records))
        return p

    def test_validation_and_corruption_detection(self):
        manifest = self.manifest()
        good = self.csv([dict(va="0x1080", pa="0x5080")])
        self.assertTrue(validate(good, manifest, min_samples=1, require_pa=True, min_coverage=0)["passed"])
        self.assertFalse(validate(good, manifest, min_samples=1, require_pa=True)["passed"])
        for i, bad in enumerate([dict(va="0x1080", pa="0x6080"),
                                 dict(va="0x1080", pa="0x5081"),
                                 dict(va="0x2000"), dict(va="0x9000"), dict(pid=2), dict(pa="0x0")]):
            p = self.csv([bad], f"bad{i}.csv")
            with self.subTest(bad=bad):
                self.assertFalse(validate(p, manifest, min_samples=1, require_pa=True, min_coverage=0)["passed"])

    def test_real_workload_manifest(self):
        binary = ROOT / "build/memory_phases"
        if not binary.exists():
            self.skipTest("run make first")
        manifest = self.path / "actual.jsonl"
        subprocess.run([str(binary), str(manifest), "0.03"], check=True, capture_output=True)
        records = [json.loads(x) for x in manifest.read_text().splitlines()]
        phases = [r for r in records if r["type"] == "phase"]
        regions = [r for r in records if r["type"] == "region"]
        self.assertEqual(len(phases), 4)
        self.assertEqual(len(regions), 2)
        for phase in phases:
            self.assertGreaterEqual(phase["end_ns"] - phase["start_ns"], 30_000_000)
        self.assertTrue(all(a["end_ns"] < b["start_ns"] for a,b in zip(phases, phases[1:])))
        self.assertEqual({r["when"] for r in records if r["type"] == "page"}, {"before", "after"})

    def test_cli_failed_parse_does_not_publish_csv(self):
        src = self.path / "bad.txt"
        src.write_text("invalid\n")
        out = self.path / "bad.csv"
        p = subprocess.run([sys.executable, str(ROOT / "profile.py"), "parse", str(src), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(out.exists())
        self.assertIn("line 1", p.stderr)


class CaptureTests(unittest.TestCase):
    def args(self, **updates):
        return argparse.Namespace(**(dict(command=["--", "echo", "a b;literal"], pid=None,
            cgroup=None, system=False, duration=None, cpus=None, event="ibs_op//", period=None,
            freq=2000) | updates))

    def test_command_is_argv_not_shell(self):
        cmd = build_command(self.args(), "perf.data")
        self.assertEqual(cmd[-3:], ["--", "echo", "a b;literal"])
        self.assertIn("--phys-data", cmd)
        self.assertEqual(cmd[cmd.index("--clockid")+1], "mono")

    def test_record_keeps_successful_and_failed_captures_private(self):
        with tempfile.TemporaryDirectory() as temp:
            for code in (0, 7):
                with self.subTest(returncode=code):
                    out = Path(temp) / f"capture-{code}"
                    args = self.args(out=str(out), keep_raw=False, no_plot=True)
                    old_umask = os.umask(0)
                    try:
                        with patch('ibs.capture.check', return_value={'ibs_op': True}), \
                             patch('ibs.capture.subprocess.run', return_value=subprocess.CompletedProcess([], code)), \
                             patch('ibs.capture.extract', return_value={'samples': 1}), \
                             contextlib.redirect_stdout(io.StringIO()):
                            if code:
                                with self.assertRaisesRegex(RuntimeError, 'perf/workload failed'):
                                    record(args)
                            else:
                                record(args)
                    finally:
                        os.umask(old_umask)
                    self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
                    metadata = json.loads((out / 'metadata.json').read_text())
                    self.assertEqual(metadata['status'], 'failed' if code else 'complete')

    def test_pid_and_system(self):
        cmd = build_command(self.args(command=[], pid="123,456", duration=3), "x")
        self.assertIn("123,456", cmd)
        self.assertEqual(cmd[-3:], ["--", "sleep", "3"])
        cmd = build_command(self.args(command=[], system=True, duration=3, cpus="0-1", period=100000), "x")
        self.assertIn("-a", cmd)
        self.assertIn("-c", cmd)
        self.assertNotIn("-F", cmd)

    def test_invalid_mode_combinations(self):
        for update in [dict(pid="1"), dict(duration=3), dict(command=[]),
                       dict(command=[], pid="-1", duration=3), dict(cpus="1"),
                       dict(event="cycles"), dict(command=[], system=True)]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                build_command(self.args(**update), "x")


if __name__ == "__main__":
    unittest.main()
