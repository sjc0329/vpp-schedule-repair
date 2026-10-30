"""Replay only final cost selection on the frozen 42 pilot configurations.

    Nine economic baselines are reused and reverified; 33 repair plans receive
    one additional cost minimization with the old deviation/participation caps.
    No dataset, nominal plan, reference cost, or earlier-stage optimum is changed.
"""
import csv
import hashlib
import json
from pathlib import Path
import time
import traceback

from run_pilot import clean, require
from vpp_repair.model import RepairModel
from vpp_repair.repair import refine_cost
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OLD = ROOT/"results"/"pilot_v1_corrected"
DATA = ROOT/"datasets"/"pilot_v1_corrected"
OUT = ROOT/"results"/"pilot_cost_tiebreak"
TOL = 2e-6


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,
                               allow_nan=False)+"\n",encoding="utf-8")


def main():
    if OUT.exists():
        raise SystemExit("refusing to overwrite existing refinement results")
    OUT.mkdir(parents=True)
    evidence = read(OLD/"analysis.json")["evidence_sha256"]
    require(all(sha(ROOT/p) == digest for p,digest in evidence.items()),
            "frozen pilot evidence changed")
    sources = sorted((ROOT/"vpp_repair").glob("*.py"))+[ROOT/"run_cost_refinement.py"]
    manifest = dict(purpose="Cost-only refinement of saved results; no earlier phases rerun",
        reused_baselines=9,planned_final_stage_solves=33,
        frozen_evidence_sha256=evidence,
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources})
    write(OUT/"manifest.json",manifest)
    rows,failures,results = [],[],{}
    for path in sorted((OLD/"cases").glob("*.json")):
        old = read(path)
        name = old["case_id"]
        state = read(DATA/(name+"-event-state.json"))
        result_case = dict(case_id=name,configurations={})
        for label,item in old["configurations"].items():
            try:
                oldrow,detail = item["summary"],item["detail"]
                started = time.perf_counter()
                if label == "economic":
                    plan = detail["plan"]
                    check = verify(state,plan)
                    require(check["valid"],"baseline verification failed")
                    result = dict(status="REUSED_VERIFIED_ECONOMIC_BASELINE",
                        selected_plan=plan,selected_validation=check,reused=True)
                else:
                    oldcheck = detail["selected_validation"]
                    model = RepairModel(state)
                    result = refine_cost(model,detail["selected_plan"],
                        detail["economic_reference"]["cost_cap"],
                        detail["total_cap"],oldcheck["n_adjust"])
                    check = result["selected_validation"]
                    require(model.audit_backend()["valid"],"backend audit failed")
                    result["status"] = ("COST_OPTIMAL_WITHIN_FROZEN_BANDS"
                                        if result["cost_proven_optimal"] else "VERIFIED_FALLBACK")
                    require(check["cost"] <= oldcheck["cost"]+2e-7,"cost increased")
                    require(check["d_vpp"] <= oldcheck["d_vpp"]+TOL,"plan holding worsened")
                    require(check["n_adjust"] <= oldcheck["n_adjust"],"more participants")
                    if result["phase"]["optimal"]:
                        require(abs(result["phase"]["objective"]-check["cost"]) < TOL,
                                "cost objective mismatch")
                    result["frozen_caps"] = dict(
                        cost=detail["economic_reference"]["cost_cap"],
                        total=detail["total_cap"],participation=oldcheck["n_adjust"])
                row = dict(case_id=name,method=label,status=result["status"],
                    old_cost=oldrow["cost"],new_cost=check["cost"],
                    cost_saving=oldrow["cost"]-check["cost"],
                    old_d_vpp=oldrow["d_vpp"],new_d_vpp=check["d_vpp"],
                    old_n_adjust=oldrow["n_adjust"],new_n_adjust=check["n_adjust"],
                    old_d_edge=oldrow["d_edge"],new_d_edge=check["d_edge"],
                    extra_cost_vs_common_upper=check["cost"]-
                        old["common_economic_reference"]["validation"]["cost"],
                    additional_wall_seconds=time.perf_counter()-started,
                    additional_solver_seconds=0 if label == "economic"
                        else result["phase"]["solver_seconds"],
                    cost_proven_optimal=None if label == "economic"
                        else result["cost_proven_optimal"])
                rows.append(row)
                result_case["configurations"][label] = dict(summary=row,detail=result)
                print(f"{name} {label}: saving={row['cost_saving']:.8g}; "
                      f"D={row['new_d_vpp']:.8g}; N={row['new_n_adjust']}",flush=True)
            except Exception as exc:
                error = dict(case_id=name,method=label,error=str(exc),traceback=traceback.format_exc())
                failures.append(error)
                result_case["configurations"][label] = dict(error=error)
                print(name+" "+label+" ERROR: "+str(exc),flush=True)
            write(OUT/"cases"/(name+".json"),result_case)
            write(OUT/"progress.json",dict(completed=len(rows),errors=failures))
        results[name] = result_case
    with (OUT/"comparison.csv").open("w",newline="",encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    old_witnesses = read(OLD/"analysis.json")["same_primary_metrics_but_costlier_than_zero_budget"]
    resolved = []
    for witness in old_witnesses:
        found = results[witness["case_id"]]["configurations"][witness["method"]]
        resolved.append(dict(case_id=witness["case_id"],method=witness["method"],
            resolved="summary" in found and found["summary"]["new_cost"] <= witness["witness_cost"]+TOL))
    repairs = [r for r in rows if r["method"] != "economic"]
    summary = dict(completed_configurations=len(rows),reused_verified_baselines=len(rows)-len(repairs),
        new_cost_stage_solves=len(repairs),errors=failures,
        all_completed=len(rows)==42 and not failures,
        all_cost_stages_optimal=all(r["cost_proven_optimal"] for r in repairs),
        repairs_with_cost_saving=sum(r["cost_saving"] > TOL for r in repairs),
        largest_cost_saving=max((r["cost_saving"] for r in repairs),default=0),
        previous_costlier_witnesses_resolved=sum(r["resolved"] for r in resolved),
        previous_costlier_witness_count=len(resolved),witness_checks=resolved,
        max_total_deviation_increase=max((r["new_d_vpp"]-r["old_d_vpp"] for r in repairs),default=0),
        max_participation_increase=max((r["new_n_adjust"]-r["old_n_adjust"] for r in repairs),default=0),
        internal_edge_modification_can_change=True,
        frozen_evidence_unchanged=all(sha(ROOT/p)==digest for p,digest in evidence.items()))
    require(summary["frozen_evidence_unchanged"],"frozen evidence changed during run")
    write(OUT/"summary.json",summary)
    lines = ["# 同等修复效果下的成本择优复核", "",
        "使用冻结的pilot_v1_corrected数据、发布计划、实际状态、共同经济参照及既有最优指标。",
        "9个经济基线复用并独立核验，33个修复配置各新增一次成本求解；不重新生成数据或原计划。", "",
        f"完成{len(rows)}/42；成本阶段全部最优：{summary['all_cost_stages_optimal']}。",
        f"{summary['repairs_with_cost_saving']}个修复配置成本降低，最大降低{summary['largest_cost_saving']:.6g}元。",
        f"此前11个更低成本证据已消除：{summary['previous_costlier_witnesses_resolved']}/11。",
        f"总修改最大增加{summary['max_total_deviation_increase']:.3g}kWh，参与数最大增加{summary['max_participation_increase']}。", "",
        "|案例|方法|原成本/元|新成本/元|节省/元|原总修改|新总修改|原参与数|新参与数|",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"|{r['case_id']}|{r['method']}|{r['old_cost']:.6f}|{r['new_cost']:.6f}|"
            f"{r['cost_saving']:.6f}|{r['old_d_vpp']:.6f}|{r['new_d_vpp']:.6f}|"
            f"{r['old_n_adjust']}|{r['new_n_adjust']}|")
    lines += ["", "## 解释", "",
        "最终成本是在冻结总修改上限和参与数上限下的条件最优成本，受记录的数值容差影响。",
        "若更早的修改/协调阶段未证明最优，则不能据此称为全局词典序最优；本轮原阶段均已最优。",
        "内部Edge修改不是锁定目标，成本择优后可增可减，表外指标仍完整保存于comparison.csv。",
        "新增求解开销单独记录，端到端开销需加原有各阶段开销；不宣称效率提升。",
        "原先导输入与结果均未覆盖，未新增场景或进行参数搜索。"]
    if failures:
        lines += ["", "## 错误", "", *[str(e) for e in failures]]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k != "witness_checks"},
                     ensure_ascii=False,indent=2))
    if not summary["all_completed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
