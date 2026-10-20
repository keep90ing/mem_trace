# Validation

All commands run from the directory containing `profile.py`. Generated data
is private and excluded from the public source tree; see [SECURITY.md](SECURITY.md).

## Offline checks

```bash
make test CC=/usr/bin/gcc
```

Requires Matplotlib in the Python environment used by make. Tests cover:

- Parsing, field order, nanosecond precision, unknown TIDs and invalid input.
- Synthetic VA/PA fixtures, PID filtering, address ranks and downsampling.
- Plot output, unavailable PA and failed-parse handling.
- Command argument construction without an implicit shell.
- Owner-only capture directories under a permissive umask, on success and failure.
- The bundled synthetic workload's manifest and deliberate validation errors.

Publication review, 2026-10-20: **PASS, 15 tests**, including successful and
failed captures under umask `000`. The C validation workload compiled with
`-Wall -Wextra -Werror`. These checks do not record IBS or establish hardware
support.

## Hardware checks

```bash
sudo bash validate_hardware.sh
```

Use a Python environment with Matplotlib available to the script. It records
a bundled synthetic access pattern and compares the samples with independent
VA ranges, phase times and before/after pagemap observations. A required PA
comparison fails when too few stable PFNs are visible; unavailable PA is not
reported as success.

Run this separately on each target host. Before/after pagemap snapshots cannot
exclude migration away and back between snapshots, and address-span coverage
is not a count of every page accessed. No new hardware capture was run during
the publication review.

## Independent decoding check

```bash
python3 verify_replay.py /path/to/perf.data /path/to/samples.csv \
  --out /path/to/new/replay-report.json
```

Use the unfiltered CSV extracted from that same recording. This compares
time, VA, PA and row counts against a separate perf export. A decoding match
does not validate the original capture scope, hardware accuracy, or permissions.
