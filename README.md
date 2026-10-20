# AMD IBS Access Profiler

Visualize where and when a workload accesses memory using **AMD Instruction
Based Sampling (IBS)**. Record samples with Linux `perf`, export them to CSV,
and automatically generate virtual-address (VA) and physical-address (PA)
timelines.

Works with commands, running processes, cgroups, and system-wide captures.

## Setup

Requirements: Linux x86-64 with AMD IBS, a compatible `perf`, Python 3.10+,
and Matplotlib. GCC and make are needed only for validation.

Run all commands from the directory containing `profile.py`:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python profile.py check
```

Examples use `sudo` for IBS recording and physical addresses. Checking PMU
presence does not prove recording permission; verify it with a capture.
If your system Python already has Matplotlib, use `python3` instead of
`.venv/bin/python`.

## Record and plot

### Start a command

```bash
sudo .venv/bin/python profile.py record \
  --out results/run --focus-comm my-program \
  -- /path/to/my-program arg1 arg2
```

Put profiler options before `--`. Capture ends when the command exits, then
CSV extraction and plotting run automatically. **The command inherits the
profiler's privileges**; use PID or cgroup mode to keep a workload's existing
user and environment.

### Attach to a process

```bash
sudo .venv/bin/python profile.py record \
  --out results/process --pid 12345 --duration 30
```

`--pid` also accepts comma-separated PIDs, such as `12345,12346`.

### Monitor a cgroup

```bash
sudo .venv/bin/python profile.py record \
  --out results/cgroup --duration 30 \
  --cgroup system.slice/my-service.service --cpus 0-3
```

The cgroup must exist; its path is relative to `/sys/fs/cgroup`. Monitor all
CPUs on which its workload can run. Start capture before launching the workload
in that cgroup if you need to observe startup.

### Monitor selected CPUs

```bash
sudo .venv/bin/python profile.py record \
  --out results/system --system --cpus 0-3 --duration 10
```

Choose one capture mode. PID, cgroup, and system modes require `--duration`;
ending capture leaves the external workload running. `--cpus` is available
only in cgroup and system modes.

Every `record --out` directory must be **new**. Add `--no-plot` to record and
extract only, or `--keep-raw` to also save the `perf script` text.

## Sampling options

The default event is `ibs_op//` with target frequency `--freq 2000`.
Actual sample counts depend on the workload, monitored CPUs, and perf's
frequency adjustment.

For a fixed sampling period, add these options before `--`:

```text
--event 'ibs_op/cnt_ctl=1/' --period 100000
```

Use either `--freq` or `--period`. On CPUs that expose `l3missonly`, an event
such as `ibs_op/cnt_ctl=1,l3missonly=1/` limits sampling to L3 misses. Different
filters observe different access populations; their point densities are not
directly comparable. Avoid simultaneous IBS collectors when characterizing
a workload.

## Output

```text
<output-directory>/
  perf.data                  Original perf capture
  record.log                 perf/workload output and loss warnings
  metadata.json              Command, host, timestamps, exit and parse status
  samples.csv                time_sec,comm,pid,tid,cpu,va,pa,ip,event
  plots/
    data_va_timeline.jpg
    data_pa_timeline.jpg
    plot_summary.json
```

Failed captures retain their diagnostics and return a nonzero exit status.

## Replot or import data

Replot a capture with finer address blocks and kernel VA included:

```bash
sudo .venv/bin/python profile.py plot results/run/samples.csv \
  --out results/run/plots-detail \
  --focus-comm my-program --block-bytes 65536 --include-kernel
```

Import an existing perf capture, then plot its CSV:

```bash
sudo .venv/bin/python profile.py extract /path/to/perf.data \
  --out results/import/samples.csv
sudo .venv/bin/python profile.py plot results/import/samples.csv \
  --out results/import/plots
```

Extraction refuses to overwrite CSV files; replotting updates existing plots.
Add `--pid 12345` to `plot` to filter samples, or `--max-points 800000` for
more displayed points. Use `samples.csv` for custom histograms and summaries.

## Read the plots

- **X:** seconds since the first selected sample, using `CLOCK_MONOTONIC`.
- **Y:** sorted address-block rank with gaps compressed, not linear address
  distance. Default blocks are 4 MiB; `--block-bytes 65536` uses 64 KiB.
- **Color:** red matches the case-insensitive `--focus-comm` substring; blue
  is everything else. This changes color, not capture scope. Without it,
  all selected samples are red.
- **Filtering:** zero addresses are hidden. Kernel VA requires
  `plot --include-kernel`; PA has no kernel-address filter.
- **Detail:** at most 400000 points per address/color series are displayed
  by default. Equal VA values from different PIDs need not refer to the same memory.

These are **sampled timelines**, not complete traces, exact access counts,
working-set sizes, or bandwidth measurements. Samples alone do not identify
logical file offsets or allocation types. Point density also depends on
sampling, downsampling, and display settings.

## Validation

Offline tests:

```bash
PATH="$PWD/.venv/bin:$PATH" make test CC=/usr/bin/gcc
```

Hardware validation using a bundled synthetic access pattern:

```bash
sudo env PATH="$PWD/.venv/bin:$PATH" bash validate_hardware.sh
```

The hardware check compares sampled VA/PA against recorded address ranges
and pagemap data. `verify_replay.py` separately checks CSV decoding against
an existing perf capture. See [VALIDATION.md](VALIDATION.md) for the validation
procedure and recorded offline checks; rerun hardware validation on each target host.

## Data privacy

Capture files, CSV, plots, and logs can expose address layouts, file paths,
process names, command arguments, and workload output. New `record` directories
are owner-only (`0700`), and `.gitignore` excludes generated captures and common
credential files. Review exports before sharing; see [SECURITY.md](SECURITY.md).
