"""Authorized, fixed nine-case / 42-configuration deterministic pilot."""
from copy import deepcopy
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import time
import traceback

from vpp_repair.data import apply_event
from vpp_repair.model import RepairModel
from vpp_repair.pilot_data import all_pilot_cases, event_snapshot
from vpp_repair.repair import select_repair
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/"results"/"pilot_v1"
DATA = ROOT/"datasets"/"pilot_v1"


def clean(value):
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items()}
    if isinstance(value,list):
        return [clean(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):
        return None
    return value


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,
                               allow_nan=False)+"\n",encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition,message):
    if not condition:
        raise ValueError(message)


def checked(state,result,**caps):
    require(result.get("plan") is not None,"no primal plan: "+result["status"])
    check = verify(state,result["plan"],**caps)
    require(check["valid"],"independent verification failed: "+str(check["errors"]))
    result["validation"] = check
    return check


def run_case(case,rows):
    name = case["id"]
    record = dict(case_id=name,configurations={},started=time.time())
    write(OUT/"cases"/(name+".json"),record)
    t0 = time.perf_counter()
    nominal_model = RepairModel(case)
    nominal = nominal_model.solve("economic")
    checked(case,nominal)
    record["nominal"] = nominal
    # The solved economic plan, not the placeholder, is the published reference.
    snapshot = event_snapshot(case,nominal["plan"])
    precheck = verify(snapshot,snapshot["reference"])
    require(precheck["valid"],"published snapshot failed verification")
    record["snapshot_validation"] = precheck
    record["event_states"] = {r["id"]:r["initial_energy"] for r in snapshot["resources"]}
    state,_ = apply_event(snapshot,case["event"])
    # Verify actual persistent update against a complete event-time rebuild.
    updated = RepairModel(snapshot)
    backend = id(updated.h)
    record["incremental_edits"] = updated.sync(state)
    require(id(updated.h) == backend,"backend replaced")
    rebuilt = RepairModel(state)
    require(updated.digest() == rebuilt.digest(),"incremental/rebuild mismatch")
    record["backend_audit"] = updated.audit_backend()
    require(record["backend_audit"]["valid"],"backend audit failed")
    write(DATA/(name+"-published.json"),nominal["plan"])
    write(DATA/(name+"-event-state.json"),state)
    record["preparation_wall_seconds"] = time.perf_counter()-t0

    economic_started = time.perf_counter()
    economic = updated.solve("economic")
    econ_check = checked(state,economic)
    record["common_economic_reference"] = economic
    upper = econ_check["cost"]
    reference_wall = time.perf_counter()-economic_started

    def add(label,detail,check,epsilon,kcap,wall,phases):
        row = dict(case_id=name,family=case["family"],method=label,
                   epsilon=epsilon,kcap=kcap,status=detail["status"],
                   valid=bool(check and check["valid"]),
                   cost=check["cost"] if check else None,
                   extra_cost_vs_common_upper=check["cost"]-upper if check else None,
                   d_vpp=check["d_vpp"] if check else None,
                   d_edge=check["d_edge"] if check else None,
                   n_adjust=check["n_adjust"] if check else None,
                   wall_seconds_excluding_shared_reference=wall,
                   solver_seconds_excluding_shared_reference=sum(
                       p["solver_seconds"] for n,p in phases.items()
                       if n != "economic" or not p.get("reused_common_reference")),
                   shared_reference_wall_seconds=reference_wall,
                   economic_reference_optimal=economic["optimal"],
                   total_optimal=detail.get("total_proven_optimal"),
                   coordination_optimal=detail.get("coordination_proven_optimal"))
        rows.append(row)
        record["configurations"][label] = dict(summary=row,detail=detail)
        write(OUT/"cases"/(name+".json"),record)
        write(OUT/"progress.json",dict(completed_configurations=len(rows),rows=rows))
        print(f"{name} {label}: {row['status']}; D={row['d_vpp']}; "
              f"extraC={row['extra_cost_vs_common_upper']}; K={row['n_adjust']}",flush=True)

    add("economic",economic,econ_check,0,3,reference_wall,{"economic":economic})
    configs = [("zero_budget",0.0,3),("budget_0.64",0.64,3),
               ("budget_1.92",1.92,3)]
    if case["level"] == 2:
        configs += [("budget_0.64_K1",0.64,1),("budget_0.64_K2",0.64,2)]
    for label,epsilon,kcap in configs:
        started = time.perf_counter()
        repair = select_repair(RepairModel(state),epsilon=epsilon,
                               participant_cap=kcap,delta_d=0,
                               shared_economic=economic)
        check = repair["selected_validation"]
        if check:
            for phase_name,phase in repair["phases"].items():
                if phase.get("validation") and phase["optimal"]:
                    metric = (phase["validation"]["cost"] if phase_name in ("economic","cost_tiebreak")
                              else phase["validation"]["d_vpp"] if phase_name == "total"
                              else phase["validation"]["n_adjust"])
                    require(abs(phase["objective"]-metric) < 2e-5,
                            "solver/checker objective mismatch: "+phase_name)
        add(label,repair,check,epsilon,kcap,time.perf_counter()-started,repair["phases"])
    record["finished"] = time.time()
    write(OUT/"cases"/(name+".json"),record)


def report(rows,failures,manifest):
    summary = dict(planned_cases=9,planned_configurations=42,
        completed_configurations=len(rows),valid_plans=sum(r["valid"] for r in rows),
        case_errors=failures,all_completed=len(rows)==42 and not failures,
        purpose="Deterministic single-event pilot; not independent statistical samples",
        manifest=manifest)
    write(OUT/"summary.json",summary)
    if rows:
        with (OUT/"configurations.csv").open("w",newline="",encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# 路线 A：九案例先导实验", "",
        f"计划42个配置，完成{len(rows)}个；独立核验可行计划{summary['valid_plans']}个。", "",
        "确定性自建案例，不是统计独立样本。全部输入、失败和求解状态保留。",
        "原计划为事件前完整日经济调度，事件状态由实际前缀截取；各方法共享事件后经济参照。",
        "零预算和正预算方法均在总修改阶段后执行协调择优，delta_d=0。",
        "成本和修改量仅统计事件后的剩余时域；完整轨迹包含已执行部分。", "",
        "|案例|方法|状态|总修改/kWh|内部修改/kWh|额外成本/元|参与数|",
        "|---|---|---|---:|---:|---:|---:|"]
    def fmt(value):
        return "—" if value is None else f"{value:.6g}"
    for r in rows:
        lines.append(f"|{r['case_id']}|{r['method']}|{r['status']}|{fmt(r['d_vpp'])}|"
                     f"{fmt(r['d_edge'])}|{fmt(r['extra_cost_vs_common_upper'])}|{fmt(r['n_adjust'])}|")
    lines += ["", "## 开销与解释", "",
        "完整日计划生成与状态/矩阵审核属于准备开销，逐例单独记录。",
        "经济参照只求一次并共享；每个修复配置记录新建模型及后续阶段开销，",
        "端到端使用成本需再加该例共享经济参照开销，不能据此宣称速度优势。",
        "每个阶段保留原始上下界、状态与独立核验；时间未完成不等同不可行。",
        "未对参数进行结果导向调整，未增加其他事件强度或连续事件实验。"]
    if failures:
        lines += ["", "## 执行错误", "", *[f"- {f['case_id']}: {f['error']}" for f in failures]]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k != "manifest"},
                     ensure_ascii=False,indent=2),flush=True)


def main():
    global OUT,DATA
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id",default="pilot_v1")
    args = parser.parse_args()
    if not args.run_id.replace("_","").isalnum():
        raise SystemExit("run-id must contain only letters, numbers and underscores")
    OUT = ROOT/"results"/args.run_id
    DATA = ROOT/"datasets"/args.run_id
    if OUT.exists() or DATA.exists():
        raise SystemExit("run already exists; refusing to overwrite frozen inputs/results")
    cases = all_pilot_cases()
    DATA.mkdir(parents=True)
    OUT.mkdir(parents=True)
    for case in cases:
        write(DATA/(case["id"]+"-input.json"),case)
    source_files = sorted((ROOT/"vpp_repair").glob("*.py"))+[ROOT/"run_pilot.py"]
    manifest = dict(recipe="pilot_v1; frozen before solving",run_id=args.run_id,configurations=42,
        input_sha256={p.name:sha(p) for p in sorted(DATA.glob("*-input.json"))},
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in source_files},
        note="Nominal placeholder reference is not a published plan; solved plan saved separately")
    write(DATA/"manifest.json",manifest)
    rows,failures = [],[]
    for case in cases:
        try:
            run_case(case,rows)
        except Exception as exc:
            error = dict(case_id=case["id"],error=str(exc),traceback=traceback.format_exc())
            failures.append(error)
            write(OUT/"errors"/(case["id"]+".json"),error)
            print(case["id"]+" ERROR: "+str(exc),flush=True)
    report(rows,failures,manifest)
    if failures or len(rows) != 42:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
