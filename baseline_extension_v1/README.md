# Literature-baseline extension: calibration prevalidation

This directory contains two core-mechanism adaptations and their prevalidation on
13 existing calibration cases only. Original source code, datasets, result archives,
and the manuscript remain unchanged. No formal 180-configuration experiment runs here.

- SEGAN 2024: cost/deviation weighted dispatch, followed by attempting to put one
  high adjustable-resource-cost non-event Edge on backup. Its controls stay at the
  published values; all Edges remain in the aggregate commitment. The backup solve
  retains the baseline first-pass deviation level and the common physical cost cap.
  Cost ranking and the backup guard are explicit adaptations; this is not the original
  paper's full independent-microgrid market/control system.
- IET GTD 2026: tariff-consistent asymmetric settlement. Physical grid costs are
  counted once, with additional unfavorable-change charges/reduced favorable-change
  credit. No forecasting network or rolling controller is reconstructed.

The frozen protocol in results/protocol.json records grids, source cases and
original-file hashes before solving. Parameters: SEGAN lambda=0.8 times 1/10/100;
settlement beta_minus=1+0.3*kappa and beta_plus=1-0.2*kappa, kappa=0.5/1/2.
Both use alpha=0.1 and 0.3, with all physical constraints and original tolerances.

The 156 outputs require at most 234 fresh solves; 13 verified, recorded optimal
cost anchors are reused. A higher 247-call ceiling allowed for reference replacement
if needed. Each solve has a 20-second limit. Report total solver calls and summed
solver times; these are not fixed equal total-CPU-budget comparisons.

results/metrics.csv contains D, physical C-U, strict and engineering participation,
internal Edge modification, and backup diagnostics. results/cases/ retains plans,
subproblem states and independent checks. A time-limited backup without an incumbent
falls back to the initial plan; this is distinguished from proven infeasibility.
A successful backup solve can increase another Edge's participation; no minimum-N
property is assumed. Subproblem optimality is separate from global policy optimality.

Parameters are ranked only from these calibration cases, using family-balanced
D/event-energy, strict N/non-event-Edge count, and extra cost/(0.8 event-energy).
The proposed method's outputs and formal comparison outcomes are not used to rank.
A parameter recommendation is not a globally optimal hyperparameter claim.
