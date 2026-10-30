"""Six frozen absorption cases, 24 configurations, plus constructive checks."""
import argparse
import csv
import json
from pathlib import Path
import time
import traceback

from run_pilot import require,write,sha,checked
from vpp_repair.absorption import certify_absorption
from vpp_repair.absorption_data import all_absorption_cases
from vpp_repair.data import apply_event
from vpp_repair.model import RepairModel
from vpp_repair.pilot_data import event_snapshot
from vpp_repair.repair import select_repair
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id",default="absorption_v1")
    args = parser.parse_args()
    require(args.run_id.replace("_","").isalnum(),"invalid run id")
    out,data = ROOT/"results"/args.run_id,ROOT/"datasets"/args.run_id
    require(not out.exists() and not data.exists(),"refusing to overwrite existing evidence")
    out.mkdir(parents=True)
    data.mkdir(parents=True)
    cases = all_absorption_cases()
    for case in cases:
        write(data/(case["id"]+"-input.json"),case)
    sources = sorted((ROOT/"vpp_repair").glob("*.py"))+[
        ROOT/"run_absorption.py",ROOT/"run_pilot.py"]
    manifest = dict(recipe="six predeclared absorption variants",planned_configurations=24,
        inputs_frozen_before_solving=True,run_id=args.run_id,
        input_sha256={p.name:sha(p) for p in sorted(data.glob("*-input.json"))},
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources})
    write(data/"manifest.json",manifest)
    rows,errors,certificates = [],[],[]
    for case in cases:
        name = case["id"]
        record = dict(case_id=name,configurations={})
        def save():
            write(out/"cases"/(name+".json"),record)
            write(out/"progress.json",dict(completed=len(rows),errors=errors))
        try:
            start = time.perf_counter()
            nominal = RepairModel(case).solve("economic")
            checked(case,nominal)
            record["nominal"] = nominal
            snapshot = event_snapshot(case,nominal["plan"])
            require(verify(snapshot,snapshot["reference"])["valid"],"invalid publication snapshot")
            updated = RepairModel(snapshot)
            backend = id(updated.h)
            state,_ = apply_event(snapshot,case["event"])
            record["incremental_edits"] = updated.sync(state)
            require(id(updated.h) == backend,"backend replaced")
            require(updated.digest()==RepairModel(state).digest(),"incremental/rebuild mismatch")
            record["backend_audit"] = updated.audit_backend()
            require(record["backend_audit"]["valid"],"backend audit failed")
            write(data/(name+"-published.json"),nominal["plan"])
            write(data/(name+"-event-state.json"),state)
            certificate = certify_absorption(state)
            record["certificate"] = certificate
            certificates.append(dict(case_id=name,absorption_energy=certificate["absorption_energy"],
                local_only_energy=certificate["local_only_energy"],
                minimum_direct_support_edges=certificate["minimum_direct_support_edges"],
                direct_absorption_sufficient=certificate["direct_absorption_sufficient"]))
            record["preparation_wall_seconds"] = time.perf_counter()-start
            save()
            started = time.perf_counter()
            economic = updated.solve("economic")
            econ_check = checked(state,economic)
            reference_wall = time.perf_counter()-started
            record["common_economic_reference"] = economic
            upper = econ_check["cost"]

            def add(label,detail,check,epsilon,wall):
                witness = certificate["witness_validation"]
                allowed = witness is not None and witness["cost"] <= upper+epsilon+2e-7
                if allowed and label != "economic":
                    require(check is not None,"missed feasible certificate witness")
                    if detail.get("total_proven_optimal"):
                        require(check["d_vpp"] <= 2e-6,"failed certified zero modification")
                row = dict(case_id=name,family=case["family"],method=label,epsilon=epsilon,
                    status=detail["status"],valid=bool(check and check["valid"]),
                    cost=check["cost"] if check else None,
                    extra_cost=check["cost"]-upper if check else None,
                    d_vpp=check["d_vpp"] if check else None,
                    d_edge=check["d_edge"] if check else None,
                    n_adjust=check["n_adjust"] if check else None,
                    certificate_energy=certificate["absorption_energy"],
                    certificate_sufficient=certificate["direct_absorption_sufficient"],
                    certificate_budget_allowed=allowed,
                    certificate_cost_gap=(witness["cost"]-upper if witness else None),
                    wall_seconds_excluding_shared_reference=wall,
                    shared_reference_wall_seconds=reference_wall,
                    total_optimal=detail.get("total_proven_optimal"),
                    coordination_optimal=detail.get("coordination_proven_optimal"),
                    cost_optimal=detail.get("cost_proven_optimal"))
                rows.append(row)
                record["configurations"][label] = dict(summary=row,detail=detail)
                save()
                print(f"{name} {label}: D={row['d_vpp']}; cost+={row['extra_cost']}; "
                      f"N={row['n_adjust']}; certificate={allowed}",flush=True)

            add("economic",economic,econ_check,0,reference_wall)
            for label,epsilon in (("zero_budget",0),("budget_0.64",0.64),("budget_1.92",1.92)):
                started = time.perf_counter()
                repair = select_repair(RepairModel(state),epsilon=epsilon,
                    delta_d=0,shared_economic=economic)
                check = repair["selected_validation"]
                if check:
                    for phase_name,phase in repair["phases"].items():
                        if phase.get("validation") and phase["optimal"]:
                            metric = (phase["validation"]["cost"]
                                      if phase_name in ("economic","cost_tiebreak") else
                                      phase["validation"]["d_vpp"] if phase_name=="total" else
                                      phase["validation"]["n_adjust"])
                            require(abs(phase["objective"]-metric) < 2e-5,
                                    "solver/checker objective mismatch")
                add(label,repair,check,epsilon,time.perf_counter()-started)
        except Exception as exc:
            error = dict(case_id=name,error=str(exc),traceback=traceback.format_exc())
            errors.append(error)
            record["error"] = error
            save()
            print(name+" ERROR: "+str(exc),flush=True)
    summary = dict(planned_cases=6,planned_configurations=24,completed_configurations=len(rows),
        verified_plans=sum(row["valid"] for row in rows),errors=errors,
        all_completed=len(rows)==24 and not errors,certificates=certificates,
        sufficient_certificate_cases=sum(c["direct_absorption_sufficient"] for c in certificates))
    write(out/"summary.json",summary)
    if rows:
        with (out/"configurations.csv").open("w",newline="",encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# 本地与跨Edge吸收实验", "",
        f"六例预先冻结，完成{len(rows)}/24个配置；独立核验可行计划{summary['verified_plans']}个。",
        "每例同一事件、发布计划、实际状态与经济参照；原旧实验文件未覆盖。", "",
        "## 直接吸收充分条件", "",
        "|案例|本地容量/kWh|总直接吸收容量/kWh|是否足以吸收8kWh|最少直接支援Edge|",
        "|---|---:|---:|---|---:|"]
    for c in certificates:
        lines.append(f"|{c['case_id']}|{c['local_only_energy']:.6g}|{c['absorption_energy']:.6g}|"
                     f"{c['direct_absorption_sufficient']}|{c['minimum_direct_support_edges']}|")
    lines += ["", "## 完整优化", "",
        "|案例|方法|总修改/kWh|内部修改/kWh|额外成本/元|参与数|证据方案在预算内|",
        "|---|---|---:|---:|---:|---:|---|"]
    def fmt(value):
        return "—" if value is None else f"{value:.6g}"
    for r in rows:
        lines.append(f"|{r['case_id']}|{r['method']}|{fmt(r['d_vpp'])}|{fmt(r['d_edge'])}|"
                     f"{fmt(r['extra_cost'])}|{fmt(r['n_adjust'])}|{r['certificate_budget_allowed']}|")
    lines += ["", "## 解释范围", "",
        "证书固定储能、EV和旧任务，仅回收弃电；证书不满足不代表完整模型不可修复。",
        "证书最小参与数只在此直接吸收子类中成立，不是完整优化的参与数下界。",
        "证书成本等于原计划剩余成本，是否满足事件后经济预算另行核验。",
        "若证书在预算内，且完整优化证明最优，则应得到零总计划修改。",
        "峰值参数在求解前冻结，保留所有案例，未进行结果导向调整。",
        "准备、共享经济参照与各阶段开销单独保存；多阶段开销不能省略。"]
    if errors:
        lines += ["", "## 错误", "", *[str(e) for e in errors]]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if not summary["all_completed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
