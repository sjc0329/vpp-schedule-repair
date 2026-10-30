# Reproducibility guide

## Core distribution

The current branch contains the model and baseline source, 85 event-state inputs, and saved numerical summaries. These inputs include physical conditions and published references. The data in `source_data/` and `tables/` support checking the reported means, paired differences, and budget thresholds. CSV and JSON records can be read without installing solvers.

Run from the repository root:

```text
python -B verify_release.py
```

This read-only check validates the 147 checksummed payload files. It performs no optimization. The checksum manifest itself is excluded from its own checksum map.

## Complete experimental snapshot

For the historical batch runners, cached economic references, complete solution trajectories, and model-update audit records, use manuscript evidence commit `d8f285987d9e45640b34feab85f30babe67afb41`:

```text
git clone https://github.com/sjc0329/vpp-schedule-repair.git vpp-schedule-repair-full
git -C vpp-schedule-repair-full checkout d8f285987d9e45640b34feab85f30babe67afb41
```

Use a separate working copy for execution. The historical runners protect saved outputs from being overwritten and consume earlier stage records. Read each runner's input and output paths before preparing new output directories. The two adaptation source files retained in the core branch also refer to historical calibration and main-batch records; execute their original workflow from the complete snapshot.

## Recorded environment

Windows, CPython 3.11, NumPy 1.24.3, HiGHS/highspy 1.15.1, PySCIPOpt 6.3.0, and SCIP 10.0.2. The requirements files record the Python dependencies; verify the SCIP backend version in the execution environment. Solver ties and timings can vary by platform.

The core-branch curation changed file organization and documentation only. It did not rerun optimization or change numerical evidence. MATLAB scripts, rendered figures, editable figure sources, progress files, and intermediate solve records are excluded from the current core distribution; the complete historical commit remains accessible. Historical whole-workspace provenance hashes in that snapshot describe the original working environment; the current distribution manifest checks the files in the selected distribution.
