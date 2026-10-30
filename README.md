# VPP schedule repair: core research materials

Code, constructed inputs, and numerical evidence for **Preserving published schedules in virtual power plants through budget-constrained resource coordination**.

Authors: Fei Chen and Jiancheng Sun, School of Control and Computer Engineering, North China Electric Power University. Correspondence: 18396727409@163.com.

## Contents

The current branch contains 148 files focused on the paper's core materials.

| Location | Contents |
|---|---|
| `vpp_repair/` | Physical model, staged repair, verification, and baseline model definitions |
| `datasets/` | 85 event-state inputs, including published references: 60 formal cases, 12 sensitivity variants, and 13 calibration cases |
| `source_data/` | Per-configuration numerical data supporting comparisons, ablations, sensitivity analysis, and selected trajectories |
| `tables/` | Model parameters and manuscript summary tables |
| `results/` | Retained numerical summaries, metrics, and calibration settings |
| `baseline_extension_v1/` | Adaptation source and summary data for adaptive participation and asymmetric settlement |
| `docs/REPRODUCIBILITY.md` | Evidence inspection and complete-snapshot execution instructions |
| `SHA256_MANIFEST.json` | Checksums of the current core distribution |

This branch does not distribute MATLAB plotting scripts, manuscript figure files, or editable figure sources. The numerical data underlying the figures remain included.

## Evidence and access

The study uses 60 constructed deterministic cases at three budgets (180 repair configurations), 166 proven-optimal tracking pairs, 180 optimal scalar pairs, 540 additional comparison outputs, and 341 sensitivity configurations. Of the sensitivity configurations, 198 evaluate the standby analytical relationship. Budget settings and overlapping batches are repeated conditions, not additional independent cases.

The complete manuscript evidence and original experiment execution files remain available in [commit `d8f2859`](https://github.com/sjc0329/vpp-schedule-repair/tree/d8f285987d9e45640b34feab85f30babe67afb41). This snapshot retains full trajectories, model-update audits, solver records, and the historical batch runners. Current summaries and numerical inputs retain their recorded values. The core branch is intended for inspecting models and numerical findings; use the complete snapshot for the original batch execution and detailed trajectory audits.

Run `python -B verify_release.py` to check the core distribution without invoking an optimizer. See the reproducibility guide for the complete-snapshot workflow.

## Licenses and citation

Author-written Python code is licensed under MIT (`LICENSE`). Constructed inputs, parameters, and numerical results are licensed under CC BY 4.0 (`LICENSE-DATA.md`). Third-party dependencies retain their own licenses. `CITATION.cff` provides author and repository metadata. Cite the manuscript evidence commit above when identifying the original experimental snapshot.
