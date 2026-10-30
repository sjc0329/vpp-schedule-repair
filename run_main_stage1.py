"""Authorized first main experiment: 12 frozen plans x 3 events x 4 configs.

    No nominal plan regeneration, literature baseline, ablation, parameter search,
    new solver installation, or changes to the frozen template files.
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
from vpp_repair.pilot_data import event_snapshot
from vpp_repair.repair import select_repair
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT/"datasets"/"formal_templates_v1"
DATA = ROOT/"datasets"/"main_stage1_v1"
OUT = ROOT/"results"/"main_stage1_v1"
ENERGIES = (4.0,6.0,8.0)
ALPHAS = (("alpha_0",0.0),("alpha_0p1",0.1),("alpha_0p3",0.3))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def freeze_inputs():
    require(not DATA.exists() and not OUT.exists(),"refusing to overwrite main experiment")
    evidence = read(ROOT/"results"/"formal_templates_v1"/"audit.json")["evidence_sha256"]
    old_files = {p:digest for p,digest in evidence.items()
                 if p.replace("\\","/").startswith(("datasets/","results/"))}
    require(all(sha(ROOT/p)==digest for p,digest in old_files.items()),"template evidence changed")
    DATA.mkdir(parents=True)
    OUT.mkdir(parents=True)
    variants = []
    for path in sorted(SOURCE.glob("*-input.json")):
        case = read(path)
        template = case["id"]
        published_path = SOURCE/(template+"-published.json")
        published = read(published_path)
        # Store an unchanged byte copy once, retaining the original plan identity.
        target = DATA/"shared_publications"/(template+".json")
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(published_path.read_bytes())
        for energy in ENERGIES:
            name = f"{template}-E{int(energy):02d}"
            original = deepcopy(case)
            original.update(id=name,source_template=template,qualification_only=False,
                            experimental_stage="main_stage1")
            original["event"]["id"] = name+"-event"
            original["event"]["task"]["energy"] = energy
            reference = deepcopy(published)
            reference["case_id"] = name  # Identity only: physical arrays unchanged.
            snapshot = event_snapshot(original,reference)
            require(verify(snapshot,reference)["valid"],"invalid frozen publication snapshot")
            state,_ = apply_event(snapshot,original["event"])
            write(DATA/(name+"-pre-event.json"),snapshot)
            write(DATA/(name+"-event-state.json"),state)
            variants.append(dict(case_id=name,template=template,energy=energy,
                                 source_publication_sha256=sha(published_path)))
    require(len(variants)==36,"unexpected case count")
    sources = sorted((ROOT/"vpp_repair").glob("*.py"))+[ROOT/"run_main_stage1.py",ROOT/"run_pilot.py"]
    manifest = dict(stage="Own-method main experiment, first event-strength batch",
        templates=12,case_count=36,configurations=144,
        energy_levels=ENERGIES,alpha_levels=[a for _,a in ALPHAS],reference_price=0.8,
        budget_rule="epsilon=alpha*0.8*event_energy",delta_d=0,
        all_inputs_frozen_before_solving=True,nominal_plans_recomputed=False,
        planned_solver_calls=360,variants=variants,
        input_sha256={str(p.relative_to(DATA)):sha(p) for p in sorted(DATA.rglob("*.json"))},
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},
        prior_template_evidence_sha256=old_files)
    write(DATA/"manifest.json",manifest)
    return variants,manifest


def main():
    variants,manifest = freeze_inputs()
    rows,errors,calls = [],[],0
    run_started = time.perf_counter()
    for variant in variants:
        name,template,energy = variant["case_id"],variant["template"],variant["energy"]
        snapshot = read(DATA/(name+"-pre-event.json"))
        state = read(DATA/(name+"-event-state.json"))
        record = dict(case_id=name,template=template,family=state["family"],energy=energy,
                      configurations={},source_publication_sha256=variant["source_publication_sha256"])
        def save():
            write(OUT/"cases"/(name+".json"),record)
            write(OUT/"progress.json",dict(completed=len(rows),solver_calls=calls,
                case_id=name,elapsed_seconds=time.perf_counter()-run_started,errors=errors))
        try:
            prepared = time.perf_counter()
            certificate = certify_absorption(state)
            record["certificate"] = certificate
            updated = RepairModel(snapshot)
            backend = id(updated.h)
            record["incremental_edits"] = updated.sync(state)
            require(id(updated.h)==backend,"backend replaced")
            require(updated.digest()==RepairModel(state).digest(),"incremental/rebuild mismatch")
            record["backend_audit"] = updated.audit_backend()
            require(record["backend_audit"]["valid"],"backend audit failed")
            preparation_wall = time.perf_counter()-prepared
            record["preparation_wall_seconds"] = preparation_wall
            reference_started = time.perf_counter()
            calls += 1
            economic = updated.solve("economic")
            reference_wall = time.perf_counter()-reference_started
            if economic["plan"] is not None:
                reference_validation_started = time.perf_counter()
                econ_check = checked(state,economic)
                reference_wall += time.perf_counter()-reference_validation_started
            else:
                econ_check = None
            record["common_economic_reference"] = economic
            record["common_economic_wall_seconds"] = reference_wall
            save()

            def add(label,detail,check,alpha,epsilon,wall):
                upper = econ_check["cost"] if econ_check else None
                witness = certificate["witness_validation"]
                witness_allowed = (witness is not None and upper is not None
                                   and witness["cost"]<=upper+epsilon+2e-7)
                if check and witness_allowed and label!="economic" and detail.get("total_proven_optimal"):
                    require(check["d_vpp"]<2e-6,"missed certified zero-modification plan")
                local_phases = ({"economic":economic} if label=="economic" else
                    {k:v for k,v in detail["phases"].items() if k!="economic"})
                stage_solver_seconds = sum(p["solver_seconds"] for p in local_phases.values())
                row = dict(case_id=name,template=template,family=state["family"],energy=energy,
                    method=label,alpha=alpha,epsilon=epsilon,status=detail["status"],
                    valid=bool(check and check["valid"]),
                    cost=check["cost"] if check else None,
                    extra_cost=check["cost"]-upper if check and upper is not None else None,
                    d_vpp=check["d_vpp"] if check else None,
                    d_vpp_per_event_kwh=check["d_vpp"]/energy if check else None,
                    d_edge=check["d_edge"] if check else None,
                    n_adjust=check["n_adjust"] if check else None,
                    economic_optimal=economic["optimal"],
                    total_optimal=detail.get("total_proven_optimal"),
                    coordination_optimal=detail.get("coordination_proven_optimal"),
                    cost_optimal=detail.get("cost_proven_optimal"),
                    fallback_used=detail.get("fallback_used",False),
                    certificate_energy=certificate["absorption_energy"],
                    certificate_budget_allowed=witness_allowed,
                    preparation_wall_seconds=preparation_wall,
                    shared_economic_wall_seconds=reference_wall,
                    config_wall_excluding_shared_reference=wall,
                    config_solver_seconds_excluding_shared_reference=stage_solver_seconds,
                    event_to_verified_wall_seconds=(preparation_wall+reference_wall+
                                                   (wall if label!="economic" else 0)),
                    certificate_diagnostic_included=True,
                    full_rebuild_diagnostic_included=True,
                    nominal_publication_reused=True)
                rows.append(row)
                record["configurations"][label] = dict(summary=row,detail=detail)
                save()
                print(f"{len(rows):03d}/144 {name} {label}: {row['status']}; "
                      f"D={row['d_vpp']}; cost+={row['extra_cost']}; N={row['n_adjust']}",flush=True)

            add("economic",economic,econ_check,None,0,reference_wall)
            for label,alpha in ALPHAS:
                epsilon = alpha*0.8*energy
                if econ_check is None:
                    status = "PHYSICALLY_INFEASIBLE" if economic["proven_infeasible"] else "UNKNOWN_ECONOMIC_REFERENCE"
                    add(label,dict(status=status,phases={}),None,alpha,epsilon,0)
                    continue
                started = time.perf_counter()
                try:
                    repair = select_repair(RepairModel(state),epsilon=epsilon,
                        delta_d=0,shared_economic=economic)
                    local_phases = {k:v for k,v in repair["phases"].items() if k!="economic"}
                    calls += len(local_phases)
                    check = repair["selected_validation"]
                    for phase_name,phase in repair["phases"].items():
                        if phase.get("validation") and phase["optimal"]:
                            metric = (phase["validation"]["cost"] if phase_name in ("economic","cost_tiebreak")
                                      else phase["validation"]["d_vpp"] if phase_name=="total"
                                      else phase["validation"]["n_adjust"])
                            require(abs(phase["objective"]-metric)<2e-5,"solver/checker objective mismatch")
                    add(label,repair,check,alpha,epsilon,time.perf_counter()-started)
                except Exception as exc:
                    error = dict(case_id=name,method=label,error=str(exc),traceback=traceback.format_exc())
                    errors.append(error)
                    add(label,dict(status="ERROR",phases={},error=error),None,alpha,epsilon,
                        time.perf_counter()-started)
        except Exception as exc:
            error = dict(case_id=name,error=str(exc),traceback=traceback.format_exc())
            errors.append(error)
            record["case_error"] = error
            save()
            print(name+" CASE ERROR: "+str(exc),flush=True)
    summary = dict(stage=manifest["stage"],planned_cases=36,planned_configurations=144,
        completed_configurations=len(rows),verified_plans=sum(r["valid"] for r in rows),
        errors=errors,recorded_solver_calls=calls,
        all_completed=len(rows)==144 and not errors,
        elapsed_seconds=time.perf_counter()-run_started,
        nominal_plans_recomputed=False,
        note="Strength variants share a frozen plan; not 36 independent daily profiles")
    write(OUT/"summary.json",summary)
    with (OUT/"configurations.csv").open("w",newline="",encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if not summary["all_completed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
