"""Read saved template evidence, independently verify plans, never optimize."""
import json
from pathlib import Path

from run_pilot import require,sha,write
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
DATA = ROOT/"datasets"/"formal_templates_v1"
OUT = ROOT/"results"/"formal_templates_v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    manifest = read(DATA/"manifest.json")
    require(all(sha(DATA/p)==digest for p,digest in manifest["input_sha256"].items()),"template input changed")
    require(all(sha(ROOT/p)==digest for p,digest in manifest["source_sha256"].items()),"execution source changed")
    old_abs = read(ROOT/"results"/"absorption_v1"/"audit.json")["evidence_sha256"]
    prior_outputs = {p:digest for p,digest in old_abs.items()
                     if p.replace("\\","/").startswith(("datasets/","results/"))}
    require(all(sha(ROOT/p)==digest for p,digest in prior_outputs.items()),"old absorption evidence changed")
    old_pilot = read(ROOT/"results"/"pilot_v1_corrected"/"analysis.json")["evidence_sha256"]
    require(all(sha(ROOT/p)==digest for p,digest in old_pilot.items()),"old pilot evidence changed")
    counts = dict(nominal=0,economic=0,zero_budget_probe=0,absorption_witness=0,standby_witness=0)
    max_residual = 0.0
    all_optimal = True
    diagnostics = []
    for path in sorted((OUT/"cases").glob("*.json")):
        record = read(path)
        name = record["case_id"]
        case = read(DATA/(name+"-input.json"))
        state = read(DATA/(name+"-event-state.json"))
        def check(s,plan,kind,**caps):
            nonlocal max_residual
            result = verify(s,plan,**caps)
            require(result["valid"],name+" "+kind+": "+str(result["errors"]))
            counts[kind] += 1
            max_residual = max(max_residual,max(result["residuals"].values()))
            return result
        check(case,record["nominal"]["plan"],"nominal")
        economic = check(state,record["common_economic_reference"]["plan"],"economic")
        all_optimal &= record["nominal"]["optimal"] and record["common_economic_reference"]["optimal"]
        require(record["backend_audit"]["valid"],"backend audit failed")
        for resource in state["resources"]:
            actual = state["reference"]["resources"][resource["id"]]["energy"][state["prefix"]]
            require(abs(actual-resource["initial_energy"])<2e-7,"actual state reset")
        for task in state["tasks"]:
            if task["id"] in state["reference"]["tasks"]:
                executed = sum(state["reference"]["tasks"][task["id"]][:state["prefix"]])*state["dt"]
                require(abs(task["original_energy"]-executed-task["energy"])<2e-7,"remaining task wrong")
        if "zero_budget_total_probe" in record:
            probe = record["zero_budget_total_probe"]
            check(state,probe["plan"],"zero_budget_probe",cost_cap=economic["cost"])
            all_optimal &= probe["optimal"]
        if record.get("certificate",{}).get("witness_plan") is not None:
            cert = record["certificate"]
            witness = check(state,cert["witness_plan"],"absorption_witness",total_cap=0,
                            participant_cap=cert["minimum_direct_support_edges"])
            require(cert["witness_plan"]["resources"]==state["reference"]["resources"],
                    "absorption certificate changed storage")
            require(cert["witness_plan"]["grid"]==state["reference"]["grid"],
                    "absorption certificate changed external exchange")
            require(abs(witness["cost"]-cert["reference_remaining_cost"])<2e-7,"certificate cost changed")
        if "standby_witness" in record:
            witness = record["standby_witness"]
            checked = check(state,witness["plan"],"standby_witness",total_cap=0,cost_cap=economic["cost"]+1.92)
            require(checked["n_adjust"]==record["design"]["expected_zero_deviation_participants"],
                    "standby witness count wrong")
        diagnostics.append(dict(case_id=name,status=record["status"],diagnostics=record["diagnostics"]))
    audit = dict(templates_reverified=len(diagnostics),plans_reverified=counts,
        total_plans_reverified=sum(counts.values()),maximum_raw_constraint_residual=max_residual,
        all_27_solver_calls_optimal=bool(all_optimal),frozen_inputs_unchanged=True,
        prior_evidence_unchanged=True,diagnostics=diagnostics)
    files = sorted(DATA.glob("*.json"))+sorted((OUT/"cases").glob("*.json"))+[
        OUT/"summary.json",OUT/"qualification.csv",ROOT/"audit_formal_templates.py"]
    audit["evidence_sha256"] = {str(p.relative_to(ROOT)):sha(p) for p in files}
    write(OUT/"audit.json",audit)
    print(json.dumps({k:v for k,v in audit.items() if k not in ("diagnostics","evidence_sha256")},
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
