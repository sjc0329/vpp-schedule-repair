# VPP schedule repair

Code, constructed data, saved experimental results, and figure sources for:
**Preserving published schedules in virtual power plants through budget-constrained resource coordination**.

Authors: Fei Chen and Jiancheng Sun, School of Control and Computer Engineering,
North China Electric Power University. Correspondence: 18396727409@163.com.
Repository: https://github.com/sjc0329/vpp-schedule-repair

## Study and evidence

The framework repairs published aggregate schedules under an economic allowance,
then selects non-event Edge participation and refines implementation cost.
All scenarios are constructed deterministic cases. A four-Edge, 96-period model
retains integer operating modes and resource energy obligations.

The formal evaluation contains 60 physical cases at three repair budgets (180
repair configurations). Raw main-batch logs also include one economic-reference
configuration per case: 144 + 96 = 240 logged configurations, including 60
references. These counts must not be confused with 240 proposed-method repairs.
Tracking comparisons use 166 proven-optimal matched pairs out of 180 feasible
baseline configurations; scalar comparisons use 180 optimal pairs. The sensitivity
study contains 341 configurations; 198 support the standby analytical comparison.

## File map

| Location | Contents |
|---|---|
| `vpp_repair/` | Models, repair stages, verification, and baseline adaptations |
| `datasets/` | Constructed inputs, publications, and event snapshots |
| `results/main_stage1_v1/`, `results/main_stage2_v1/` | Formal main batches |
| `results/tracking_comparison_v1/`, `results/scalar_comparison_v1/` | Comparative experiments |
| `results/formal_ablations_v1/` | Objective-stage ablations |
| `results/parameter_sensitivity_v1/` | Budget and response-fee sensitivity |
| Other `results/` directories | Earlier qualification, calibration, diagnostic and mechanism evidence |
| `source_data/` | Eight figure source CSV files |
| `tables/` | Parameter and numerical summary CSV files |
| `matlab/make_figures.m` | MATLAB R2024b plotting source for Figures 2–5 |
| `figures/` | PDF/PNG figures, editable MATLAB FIG files and Fig.1 draw.io source |
| `docs/REPRODUCIBILITY.md` | Environment, safe inspection and rerun guidance |
| `SHA256_MANIFEST.json` | SHA-256 checksums of this distribution |

Historical pilot failures are retained as diagnostic records. They are not part of
the formal comparison statistics. Baselines reproduce cited decision mechanisms
on the shared physical model; they are adaptations rather than the original authors' software.

## Inspect first

Run `python verify_release.py` to check file integrity without installing solvers
or running optimization. Read the saved CSV and JSON results before attempting
new solves. See `docs/REPRODUCIBILITY.md` for rerun dependencies and safeguards.

## Status and citation

The code and numerical evidence are publicly available in this repository.
The manuscript evidence snapshot is commit
[`d8f2859`](https://github.com/sjc0329/vpp-schedule-repair/tree/d8f285987d9e45640b34feab85f30babe67afb41).
Subsequent documentation updates do not change that reference snapshot.
`CITATION.cff` provides author and repository metadata. The authors have approved
the licenses below. No article acceptance status or archival DOI is asserted.

## Provenance

Research code and numerical evidence were copied from the preserved candidate.
Only original local workspace prefixes in 11 historical JSON provenance/traceback
records were replaced with `<original-workspace>`. Numerical values were unchanged.
`docs/PATH_NORMALIZATION.json` lists these files. Historical embedded checksums
describe the original experiment snapshot; `SHA256_MANIFEST.json` describes this
distribution. Figure files and plotting sources are the current manuscript versions.

## Licenses

- Author-written Python and MATLAB code: **MIT**, see [LICENSE](LICENSE).
- Constructed datasets, parameters, numerical results and figure source data:
  **CC BY 4.0**, see [LICENSE-DATA.md](LICENSE-DATA.md).
- Original author-created figure content and explanatory documentation: **CC BY 4.0**.
- Third-party components and icons retain their own licenses. External Python,
  MATLAB and solver dependencies are not relicensed by this distribution.

For reuse, credit Fei Chen and Jiancheng Sun and cite the repository. Add the
archival DOI to your citation when a version is deposited; no DOI is assigned yet.

## Additional literature-mechanism comparisons

`baseline_extension_v1/` contains the frozen adaptation code, calibration records
and formal results for the SEGAN adaptive-participation and IET 2026 asymmetric-
settlement comparisons. Three settings were evaluated on the same 180 formal
configurations (540 outputs, 720 fresh solver calls). The source settlement
coefficients and calibration candidate are both retained. See
`baseline_extension_v1/formal_results/REPORT_ZH.md` and `summary.json`;
`metrics.csv` contains the matched D, physical cost and actual participation.
The extension audits recheck saved plans and objectives without solving.
These files fall under the same split code/data licenses described above.
Historical `protected_sha256` maps describe the authors' complete working snapshot;
the distribution manifest below covers the files actually distributed.
