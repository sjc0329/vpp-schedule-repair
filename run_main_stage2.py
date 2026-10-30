"""Authorized second main batch: two known curves x 12 templates x four configs.

    Profile parameters are frozen before nominal planning. They are known
    day-ahead curves, not forecast-error shocks. The original solver/model is
    unchanged; no literature baseline or ablation is executed.
"""
from copy import deepcopy
import csv
import json
from pathlib import Path
import time
import traceback

from run_pilot import require,write,sha,checked
from vpp_repair.absorption import certify_absorption
from vpp_repair.data import apply_event
from vpp_repair.model import RepairModel
from vpp_repair.pilot_data import empty_reference,event_snapshot
from vpp_repair.repair import select_repair
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT/"datasets"/"formal_templates_v1"
DATA = ROOT/"datasets"/"main_stage2_v1"
OUT = ROOT/"results"/"main_stage2_v1"
PROFILES = ("DAY_SURPLUS","CLOUD_EVENING")
ALPHAS = (("alpha_0",0.0),("alpha_0p1",0.1),("alpha_0p3",0.3))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def freeze_inputs():
    require(not DATA.exists() and not OUT.exists(),"refusing to overwrite second-batch evidence")
    original = read(ROOT/"results"/"main_stage1_v1"/"audit.json")["evidence_sha256"]
    prior = {p:d for p,d in original.items() if p.replace("\\","/").startswith(("datasets/","results/"))}
    require(all(sha(ROOT/p)==d for p,d in prior.items()),"first-batch evidence changed")
    DATA.mkdir(parents=True)
    OUT.mkdir(parents=True)
    cases = []
    for path in sorted(SOURCE.glob("*-input.json")):
        source = read(path)
        for profile in PROFILES:
            case = deepcopy(source)
            template = source["id"]
            case.update(id=template+"-"+profile,source_template=template,profile=profile,
                        qualification_only=False,experimental_stage="main_stage2")
            for k in range(case["horizon"]):
                h = k*case["dt"]
                load_factor = (0.9 if 10<=h<16 else 1.0) if profile=="DAY_SURPLUS" else (
                    1.1 if 16<=h<22 else 1.0)
                pv_factor = 1.1 if profile=="DAY_SURPLUS" else (0.65 if 11.5<=h<12.5 else 1.0)
                wind_factor = 1.0 if profile=="DAY_SURPLUS" else (0.8 if h<12 else 1.2)
                for edge in case["load"]:
                    case["load"][edge][k] *= load_factor
                    case["pv"][edge][k] *= pv_factor
                    case["wind"][edge][k] *= wind_factor
            case["profile_recipe"] = (dict(load_multiplier=0.9,load_interval=[10,16],pv_multiplier=1.1,
                wind_multiplier=1.0) if profile=="DAY_SURPLUS" else
                dict(load_multiplier=1.1,load_interval=[16,22],pv_multiplier=0.65,
                     pv_interval=[11.5,12.5],wind_multiplier_before_12=0.8,wind_multiplier_from_12=1.2))
            case["event"]["id"] = case["id"]+"-event"
            case["event"]["task"]["energy"] = 8.0
            case["reference"] = empty_reference(case)
            cases.append(case)
            write(DATA/(case["id"]+"-input.json"),case)
    sources = sorted((ROOT/"vpp_repair").glob("*.py"))+[ROOT/"run_main_stage2.py",ROOT/"run_pilot.py"]
    manifest = dict(stage="Own-method main experiment: two changed known daily curves",
        templates=12,profiles=PROFILES,cases=24,configurations=96,planned_solver_calls=264,
        event_energy=8.0,event_window=[48,50],budget_rule="epsilon=alpha*0.8*8",
        alpha_levels=[a for _,a in ALPHAS],delta_d=0,prices_and_resource_parameters_unchanged=True,
        inputs_frozen_before_solving=True,nominal_replanned_per_case=True,
        note="Deterministic designed profiles; no forecast-error model or independent random sampling",
        input_sha256={p.name:sha(p) for p in sorted(DATA.glob("*-input.json"))},
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},prior_stage1_evidence_sha256=prior)
    write(DATA/"manifest.json",manifest)
    return cases,manifest


def main():
    cases,manifest = freeze_inputs()
    rows,errors,calls = [],[],0
    started_run = time.perf_counter()
    for case in cases:
        name = case["id"]
        record = dict(case_id=name,template=case["source_template"],profile=case["profile"],
                      family=case["family"],configurations={},solve_trace=[])
        def save():
            write(OUT/"cases"/(name+".json"),record)
            write(OUT/"progress.json",dict(completed=len(rows),solver_calls=calls,
                case_id=name,elapsed_seconds=time.perf_counter()-started_run,errors=errors))

        class TracedModel(RepairModel):
            def __init__(self,state,role):
                self.trace_role = role
                super().__init__(state)

            def solve(self,objective):
                nonlocal calls
                calls += 1
                entry = dict(sequence=calls,role=self.trace_role,objective=objective)
                record["solve_trace"].append(entry)
                try:
                    result = super().solve(objective)
                    entry["result"] = result
                    save()
                    return result
                except Exception as exc:
                    entry["exception"] = str(exc)
                    save()
                    raise
        try:
            nominal_started = time.perf_counter()
            nominal = TracedModel(case,"nominal").solve("economic")
            checked(case,nominal)
            record["nominal"] = nominal
            record["nominal_wall_seconds"] = time.perf_counter()-nominal_started
            prepared = time.perf_counter()
            snapshot = event_snapshot(case,nominal["plan"])
            require(verify(snapshot,snapshot["reference"])["valid"],"invalid publication snapshot")
            updated = TracedModel(snapshot,"event_economic")
            backend = id(updated.h)
            state,_ = apply_event(snapshot,case["event"])
            record["incremental_edits"] = updated.sync(state)
            require(id(updated.h)==backend,"backend replaced")
            require(updated.digest()==RepairModel(state).digest(),"incremental/rebuild mismatch")
            record["backend_audit"] = updated.audit_backend()
            require(record["backend_audit"]["valid"],"backend audit failed")
            certificate = certify_absorption(state)
            record["certificate"] = certificate
            write(DATA/(name+"-published.json"),nominal["plan"])
            write(DATA/(name+"-pre-event.json"),snapshot)
            write(DATA/(name+"-event-state.json"),state)
            preparation_wall = time.perf_counter()-prepared
            record["preparation_wall_seconds"] = preparation_wall
            reference_started = time.perf_counter()
            economic = updated.solve("economic")
            econ_check = checked(state,economic) if economic["plan"] is not None else None
            reference_wall = time.perf_counter()-reference_started
            record["common_economic_reference"] = economic
            record["common_economic_wall_seconds"] = reference_wall
            save()

            def add(label,detail,check,alpha,epsilon,wall):
                upper = econ_check["cost"] if econ_check else None
                witness = certificate["witness_validation"]
                allowed = witness is not None and upper is not None and witness["cost"]<=upper+epsilon+2e-7
                if check and label!="economic" and allowed and detail.get("total_proven_optimal"):
                    require(check["d_vpp"]<2e-6,"missed certified zero modification")
                phases = {"economic":economic} if label=="economic" else {
                    k:v for k,v in detail["phases"].items() if k!="economic"}
                row = dict(case_id=name,template=case["source_template"],profile=case["profile"],
                    family=case["family"],energy=8.0,method=label,alpha=alpha,epsilon=epsilon,
                    status=detail["status"],valid=bool(check and check["valid"]),
                    cost=check["cost"] if check else None,
                    extra_cost=check["cost"]-upper if check and upper is not None else None,
                    d_vpp=check["d_vpp"] if check else None,
                    d_edge=check["d_edge"] if check else None,
                    n_adjust=check["n_adjust"] if check else None,
                    economic_optimal=economic["optimal"],total_optimal=detail.get("total_proven_optimal"),
                    coordination_optimal=detail.get("coordination_proven_optimal"),
                    cost_optimal=detail.get("cost_proven_optimal"),fallback_used=detail.get("fallback_used",False),
                    certificate_energy=certificate["absorption_energy"],certificate_budget_allowed=allowed,
                    nominal_wall_seconds=record["nominal_wall_seconds"],
                    preparation_wall_seconds=preparation_wall,shared_economic_wall_seconds=reference_wall,
                    config_wall_excluding_shared_reference=wall,
                    config_solver_seconds_excluding_shared_reference=sum(p["solver_seconds"] for p in phases.values()),
                    event_to_verified_wall_seconds=preparation_wall+reference_wall+(wall if label!="economic" else 0),
                    nominal_replanned=True,curves_known_before_event=True)
                rows.append(row)
                record["configurations"][label] = dict(summary=row,detail=detail)
                save()
                print(f"{len(rows):02d}/96 {name} {label}: {row['status']}; "
                      f"D={row['d_vpp']}; cost+={row['extra_cost']}; N={row['n_adjust']}",flush=True)

            add("economic",economic,econ_check,None,0,reference_wall)
            for label,alpha in ALPHAS:
                epsilon = alpha*0.8*8
                if econ_check is None:
                    status = "PHYSICALLY_INFEASIBLE" if economic["proven_infeasible"] else "UNKNOWN_ECONOMIC_REFERENCE"
                    add(label,dict(status=status,phases={}),None,alpha,epsilon,0)
                    continue
                repair_started = time.perf_counter()
                try:
                    repair = select_repair(TracedModel(state,label),epsilon=epsilon,
                                          delta_d=0,shared_economic=economic)
                    check = repair["selected_validation"]
                    for phase_name,phase in repair["phases"].items():
                        if phase.get("validation") and phase["optimal"]:
                            metric = (phase["validation"]["cost"] if phase_name in ("economic","cost_tiebreak") else
                                      phase["validation"]["d_vpp"] if phase_name=="total" else
                                      phase["validation"]["n_adjust"])
                            require(abs(phase["objective"]-metric)<2e-5,"objective/checker mismatch")
                    add(label,repair,check,alpha,epsilon,time.perf_counter()-repair_started)
                except Exception as exc:
                    error = dict(case_id=name,method=label,error=str(exc),traceback=traceback.format_exc())
                    errors.append(error)
                    add(label,dict(status="ERROR",phases={},error=error),None,alpha,epsilon,
                        time.perf_counter()-repair_started)
        except Exception as exc:
            error = dict(case_id=name,error=str(exc),traceback=traceback.format_exc())
            errors.append(error)
            record["case_error"] = error
            save()
            print(name+" CASE ERROR: "+str(exc),flush=True)
    summary = dict(stage=manifest["stage"],planned_cases=24,planned_configurations=96,
        completed_configurations=len(rows),verified_plans=sum(r["valid"] for r in rows),
        actual_solver_calls=calls,errors=errors,all_completed=len(rows)==96 and not errors,
        elapsed_seconds=time.perf_counter()-started_run,
        note="Two predeclared deterministic profiles; no statistical independence claim")
    write(OUT/"summary.json",summary)
    if rows:
        with (OUT/"configurations.csv").open("w",newline="",encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if not summary["all_completed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
