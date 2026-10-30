"""Generate and qualify 12 structures; no literature/ablation/formal batch runs.

    Solve 12 nominal and 12 common economic references; only G1 receives a
    single zero-budget total-deviation probe each. G2 certificates, G3 energy
    diagnostics and G4 constructed witnesses do not invoke extra optimizations.
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
from vpp_repair.formal_templates import all_formal_templates
from vpp_repair.model import RepairModel
from vpp_repair.pilot_data import event_snapshot
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
DATA = ROOT/"datasets"/"formal_templates_v1"
OUT = ROOT/"results"/"formal_templates_v1"


def standby_witness(state):
    plan = deepcopy(state["reference"])
    plan["version"] = state["version"]
    task = state["event"]["task"]
    plan["tasks"][task["id"]] = [0.0]*state["horizon"]
    slots = list(range(task["arrival"],task["deadline"]))
    power = task["energy"]/(len(slots)*state["dt"])
    capacities = []
    for r in state["resources"]:
        original = plan["resources"][r["id"]]
        require(max(map(abs,original["charge"]+original["discharge"])) < 2e-7,
                "standby reference unexpectedly active")
        capacities.append(sum(r["discharge_limit"][k]*state["dt"] for k in slots))
    for k in slots:
        plan["tasks"][task["id"]][k] = power
        plan["edges"]["A"]["q"][k] -= power
        remaining = power
        for r in state["resources"]:
            delivered = min(remaining,r["discharge_limit"][k])
            plan["resources"][r["id"]]["discharge"][k] += delivered
            plan["resources"][r["id"]]["mode"][k] = 0
            plan["edges"][r["edge"]]["q"][k] += delivered
            remaining -= delivered
        require(remaining < 1e-8,"standby power insufficient")
    for r in state["resources"]:
        action = plan["resources"][r["id"]]
        action["energy"][state["prefix"]] = r["initial_energy"]
        for k in range(state["prefix"],state["horizon"]):
            action["energy"][k+1] = action["energy"][k]+(
                r["eta_c"]*action["charge"][k]-action["discharge"][k]/r["eta_d"])*state["dt"]
    check = verify(state,plan,total_cap=0)
    require(check["valid"],"standby witness failed independent verification")
    # This count bound applies when the published renewable output is already
    # maximal and there is no local storage or old task in the event window.
    renewable_saturated = all(abs(state["reference"]["edges"][e]["renewable"][k]
                                 -state["pv"][e][k]-state["wind"][e][k]) < 2e-7
                              for e in state["load"] for k in slots)
    require(renewable_saturated,"unaccounted renewable support could invalidate count bound")
    require(not any(t["id"]!=task["id"] and t["arrival"]<task["deadline"]
                    and t["deadline"]>task["arrival"] for t in state["tasks"]),
            "old task overlaps event and could release demand")
    capsum,count = 0.0,0
    for capacity in sorted(capacities,reverse=True):
        capsum += capacity
        count += 1
        if capsum+1e-8 >= task["energy"]:
            break
    require(capsum+1e-8 >= task["energy"],"support energy cap insufficient")
    return dict(plan=plan,validation=check,event_energy_capacities=capacities,
        zero_total_deviation_participation_lower_bound=count,
        bound_basis="No local storage; old tasks absent; renewable saturated; remaining source power caps",
        renewable_saturated=renewable_saturated)


def main():
    require(not DATA.exists() and not OUT.exists(),"refusing to overwrite qualification evidence")
    DATA.mkdir(parents=True)
    OUT.mkdir(parents=True)
    cases = all_formal_templates()
    for case in cases:
        write(DATA/(case["id"]+"-input.json"),case)
    sources = sorted((ROOT/"vpp_repair").glob("*.py"))+[
        ROOT/"qualify_formal_templates.py",ROOT/"run_pilot.py"]
    manifest = dict(stage="Structural qualification only; not final validation dataset",
        templates=12,planned_solver_calls=27,parameter_choices_frozen_before_solving=True,
        input_sha256={p.name:sha(p) for p in sorted(DATA.glob("*-input.json"))},
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources})
    write(DATA/"manifest.json",manifest)
    rows,errors,solver_calls = [],[],0
    for case in cases:
        record = dict(case_id=case["id"],family=case["family"],design=case["design"])
        try:
            started = time.perf_counter()
            solver_calls += 1
            nominal = RepairModel(case).solve("economic")
            checked(case,nominal)
            record["nominal"] = nominal
            snapshot = event_snapshot(case,nominal["plan"])
            require(verify(snapshot,snapshot["reference"])["valid"],"invalid original snapshot")
            updated = RepairModel(snapshot)
            backend = id(updated.h)
            state,_ = apply_event(snapshot,case["event"])
            record["incremental_edits"] = updated.sync(state)
            require(id(updated.h)==backend,"backend replaced")
            require(updated.digest()==RepairModel(state).digest(),"incremental/rebuild mismatch")
            record["backend_audit"] = updated.audit_backend()
            require(record["backend_audit"]["valid"],"backend matrix audit failed")
            write(DATA/(case["id"]+"-published.json"),nominal["plan"])
            write(DATA/(case["id"]+"-event-state.json"),state)
            solver_calls += 1
            economic = updated.solve("economic")
            em = checked(state,economic)
            record["common_economic_reference"] = economic
            diag = {}
            structure = True
            if case["family"] == "G1":
                updated.set_cost_cap(em["cost"])
                solver_calls += 1
                probe = updated.solve("total")
                pm = checked(state,probe,cost_cap=em["cost"])
                record["zero_budget_total_probe"] = probe
                diag = dict(raw_economic_deviation=em["d_vpp"],
                    zero_budget_deviation=pm["d_vpp"],
                    observed_improvement=em["d_vpp"]-pm["d_vpp"],
                    probe_optimal=probe["optimal"],
                    interpretation="An observed selectable economic-band plan, not an independent generalization result")
                structure = probe["optimal"] and diag["observed_improvement"]>2e-6
            elif case["family"] == "G2":
                cert = certify_absorption(state)
                record["certificate"] = cert
                diag = dict(direct_absorption_energy=cert["absorption_energy"],
                    local_absorption_energy=cert["local_only_energy"],
                    minimum_direct_participants=cert["minimum_direct_support_edges"],
                    sufficient=cert["direct_absorption_sufficient"],
                    witness_extra_cost=(cert["witness_validation"]["cost"]-em["cost"]
                                        if cert["witness_validation"] else None))
                structure = (cert["direct_absorption_sufficient"] if case["level"] < 3
                             else not cert["direct_absorption_sufficient"])
            elif case["family"] == "G3":
                ev = next(r for r in state["resources"] if r["id"]=="b_ev")
                target = ev["requirements"][str(ev["departure"])]
                margin = ev["eta_d"]*max(0.0,ev["initial_energy"]-target)
                window = range(state["event"]["task"]["deadline"],ev["departure"])
                future_charge_input = sum(ev["charge_limit"][k]*state["dt"] for k in window)
                demand = state["event"]["task"]["energy"]
                required_input = max(0.0,target-(ev["initial_energy"]-demand/ev["eta_d"]))/ev["eta_c"]
                enough = future_charge_input+1e-8 >= required_input
                diag = dict(ev_actual_energy=ev["initial_energy"],departure_requirement=target,
                    service_safe_discharge_without_recharge=margin,
                    event_energy=demand,post_event_charge_input_capacity=future_charge_input,
                    minimum_charge_input_for_standalone_event=required_input,
                    standalone_event_energy_can_be_restored=enough,
                    interpretation="Single-resource energy/window diagnosis; not a complete plan-repair witness")
                structure = margin < demand and (not enough if case["level"]==1 else enough)
            else:
                witness = standby_witness(state)
                record["standby_witness"] = witness
                actual = witness["validation"]["n_adjust"]
                lower = witness["zero_total_deviation_participation_lower_bound"]
                expected = case["design"]["expected_zero_deviation_participants"]
                extra = witness["validation"]["cost"]-em["cost"]
                diag = dict(actual_witness_participants=actual,participation_lower_bound=lower,
                    expected_participants=expected,witness_total_deviation=witness["validation"]["d_vpp"],
                    witness_extra_cost=extra,available_test_budget=1.92,
                    source_cost_interpretation=case["design"]["cost_interpretation"])
                structure = actual==expected==lower and extra <= 1.92+2e-7
            record.update(diagnostics=diag,structure_confirmed=bool(structure),
                          status="QUALIFIED" if structure else "STRUCTURE_REVIEW_REQUIRED",
                          wall_seconds=time.perf_counter()-started)
            row = dict(case_id=case["id"],family=case["family"],status=record["status"],
                nominal_optimal=nominal["optimal"],economic_reference_optimal=economic["optimal"],
                diagnostics=json.dumps(diag,ensure_ascii=False))
            rows.append(row)
            print(case["id"]+": "+record["status"]+" "+row["diagnostics"],flush=True)
        except Exception as exc:
            error = dict(case_id=case["id"],error=str(exc),traceback=traceback.format_exc())
            errors.append(error)
            record.update(status="ERROR",error=error)
            print(case["id"]+" ERROR: "+str(exc),flush=True)
        write(OUT/"cases"/(case["id"]+".json"),record)
        write(OUT/"progress.json",dict(completed=len(rows),errors=errors,solver_calls=solver_calls))
    summary = dict(stage="Input structural qualification, not formal comparison or ablation",
        templates=12,completed=len(rows),structure_confirmed=sum(r["status"]=="QUALIFIED" for r in rows),
        structure_review_required=sum(r["status"]=="STRUCTURE_REVIEW_REQUIRED" for r in rows),
        solver_calls=solver_calls,errors=errors,all_inputs_processed=len(rows)==12 and not errors)
    write(OUT/"summary.json",summary)
    if rows:
        with (OUT/"qualification.csv").open("w",newline="",encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# 十二结构模板输入核验", "",
        "本轮只做输入构建与结构核验，不是正式主实验、文献对比或消融。",
        f"处理{len(rows)}/12，结构确认{summary['structure_confirmed']}，需复核{summary['structure_review_required']}。",
        f"求解调用{solver_calls}次：原计划、事件后经济参照及G1零预算单目标探针。", "",
        "|模板|状态|结构诊断|","|---|---|---|"]
    for r in rows:
        lines.append(f"|{r['case_id']}|{r['status']}|{r['diagnostics']}|")
    lines += ["", "## 解释", "",
        "G1探针检验经济带内的计划择优空间；不通过事后改变经济基线制造差距。",
        "G2构造方案固定旧设备和任务；不满足证书不推出完整模型不可修复。",
        "G3诊断EV服务电量和充电窗口，不能当成完整修复方案的可行性保证。",
        "G4为高响应补偿备用资源，费用1.01元/kWh高于日内最高购电价1.0元/kWh；",
        "因此经济原计划倾向不启用。事件后以补偿代价调用备用，可保持对外计划。",
        "该费用代表合同补偿与吞吐成本，不能当成通用储能退化成本估计。",
        "G4总容量100kWh、总功率20kW、初始60%和终端40%固定，仅分布不同。",
        "所有参数在求解前记录并冻结；未确认结构的模板保留，不自动调整或删除。",
        "四类模板具有针对性，后续仍需相邻条件与不同曲线验证。"]
    if errors:
        lines += ["", "## 错误", "", *[str(e) for e in errors]]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if not summary["all_inputs_processed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
