# Reproducibility guide

## Environments

The accepted numerical environment used Windows, CPython 3.11, NumPy 1.24.3,
HiGHS/highspy 1.15.1 and PySCIPOpt 6.3.0 with SCIP 10.0.2. The requirements files
record Python dependencies; separately check the SCIP backend version reported by
your installation. Solver ties, timings and numerical residuals can vary by platform.

In a dedicated Python environment, install `requirements-baselines.txt` to obtain
the recorded Python versions. Requirements installation is separate from inspection.

## Non-solving inspection

From the repository root run `python verify_release.py`. This uses only the Python
standard library and checks all distributed files against SHA-256 hashes.
CSV summaries in `tables/` and per-configuration records in `results/` can be read
without importing the solvers. Historical scripts named `audit_*.py` may write audit
files or reconstruct models; do not assume all of them are read-only checks.

## Figures

In MATLAB R2024b, from the repository root run:

```matlab
addpath('matlab'); make_figures
```

This reads saved CSV files and rewrites the Figure 2–5 outputs in `figures/`.
It does not call an optimizer. Run it in a working copy to retain the archived
outputs. Figure 1 is edited in draw.io using `figures/figure1_framework_v2.drawio`.

## Optimization entry points

The supplied runners are historical stage entry points, not a single clean-build
pipeline. Their overwrite guards intentionally reject existing evidence directories.
Do not run them directly in the archived distribution. Use a separate working copy;
read each runner's DATA/OUT paths and input assertions before preparing fresh stage
directories. Preserve upstream dependencies and their recorded checksums.

| Entry point | Upstream evidence |
|---|---|
| `run_main_stage1.py` | Qualified formal templates and their audit |
| `run_main_stage2.py` | Formal templates and nominal publications |
| `run_tracking_comparison.py` | Main-batch cases and frozen calibration |
| `run_scalar_comparison.py` | Main-batch cases and frozen calibration |
| `run_formal_ablations.py` | Main-batch cases and full-method results |
| `run_parameter_sensitivity.py` | Main batches and saved original-budget configurations |

This packaging step performed no fresh solves. Saved results support inspection
of the reported experiments; a clean-room end-to-end rerun remains unvalidated.
