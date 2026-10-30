"""Generate/read synthetic cases and run the authorized correctness suite."""
import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
import traceback

import highspy
import numpy

from vpp_repair.data import all_cases, apply_event
from vpp_repair.model import RepairModel
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
NUM_TOL = 2e-5  # analytical-objective comparison, not an economic budget


class Unresolved(RuntimeError):
    pass


def require(ok,message):
    if not ok:
        raise AssertionError(message)


def near(actual,expected,label,tol=NUM_TOL):
    require(actual is not None and abs(actual-expected) <= tol,
            f"{label}: actual={actual}, expected={expected}, tolerance={tol}")


def clean(obj):
    if isinstance(obj,dict):
        return {k:clean(v) for k,v in obj.items()}
    if isinstance(obj,list):
        return [clean(v) for v in obj]
    if isinstance(obj,float) and not math.isfinite(obj):
        return None
    return obj


def write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2,allow_nan=False),
                    encoding="utf-8")


def generate():
    folder = ROOT/"datasets"/"basic"
    folder.mkdir(parents=True,exist_ok=True)
    manifest = []
    for case in all_cases():
        path = folder/(case["id"]+".json")
        payload = json.dumps(case,ensure_ascii=False,indent=2,allow_nan=False)+"\n"
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == case,
                    "Existing dataset differs; refusing silent overwrite: "+str(path))
        else:
            path.write_text(payload,encoding="utf-8")
        manifest.append(dict(case_id=case["id"],file=path.name,
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    write_json(ROOT/"datasets"/"manifest.json",
               dict(kind="synthetic correctness fixtures, not a performance dataset",
                    count=len(manifest),cases=manifest))


def phase(model,objective,label,phases,cost_cap=None,kcap=None):
    result = model.solve(objective)
    if result["plan"] is not None:
        validation = verify(model.state,result["plan"],cost_cap,kcap)
        result["validation"] = validation
        require(validation["valid"],label+" independent validation: "+str(validation["errors"]))
        if result["optimal"]:
            metric = validation["cost"] if objective == "economic" else validation["dq"]
            near(result["objective"],metric,label+" solver/checker objective")
    phases[label] = result
    return result


def proved(result,label):
    if not result["optimal"]:
        raise Unresolved(label+": "+result["status"])
    require(result["plan"] is not None,label+": missing primal solution")
    return result["validation"]


def check_expected_status(result,expected,label):
    if expected:
        proved(result,label)
    elif not result["proven_infeasible"]:
        if not result["optimal"]:
            raise Unresolved(label+": "+result["status"])
        raise AssertionError(label+": expected infeasible, obtained feasible")


def validate_case(case):
    phases = {}
    record = dict(case_id=case["id"],group=case["group"],
                  expected=case["expected"],phases=phases,status="RUNNING")
    started = time.perf_counter()
    try:
        original = verify(case,case["reference"])
        require(original["valid"],"invalid original plan: "+str(original["errors"]))
        record["reference_validation"] = original
        incremental = RepairModel(case)
        pre = phase(incremental,"economic","pre_event_economic",phases)
        pre_metrics = proved(pre,"pre-event reference")
        near(original["cost"],pre_metrics["cost"],"original plan is economic-optimal")
        backend_identity = id(incremental.h)
        state,changed = apply_event(case,case["event"])
        require(changed,"event not applied")
        again,changed_again = apply_event(state,case["event"])
        require(not changed_again and again == state,"idempotence failed")
        record["incremental_edits"] = incremental.sync(state)
        require(id(incremental.h) == backend_identity,"backend replaced on update")
        fresh = RepairModel(state)
        record["incremental_digest"] = incremental.digest()
        record["rebuilt_digest"] = fresh.digest()
        require(incremental.digest() == fresh.digest(),"incremental/rebuild semantic mismatch")
        record["backend_audit"] = incremental.audit_backend()
        require(record["backend_audit"]["valid"],"backend matrix mismatch")
        record["state"] = state
        econ = phase(incremental,"economic","economic",phases)
        fresh_econ = phase(fresh,"economic","rebuilt_economic",phases)
        feasible = case["expected"]["feasible"]
        check_expected_status(econ,feasible,"economic")
        check_expected_status(fresh_econ,feasible,"rebuilt economic")
        if not feasible:
            record["classification"] = "PROVEN_PHYSICALLY_INFEASIBLE"
        else:
            em, fm = proved(econ,"economic"),proved(fresh_econ,"rebuilt economic")
            near(em["cost"],case["expected"]["economic_cost"],"analytical cost")
            near(em["cost"],fm["cost"],"incremental/rebuilt economic optimum")
            physical = phase(incremental,"dq","physical_min_dq",phases)
            fresh_physical = phase(fresh,"dq","rebuilt_physical_min_dq",phases)
            pm, pfm = proved(physical,"physical D"),proved(fresh_physical,"rebuilt D")
            near(pm["dq"],case["expected"]["dq_physical"],"analytical physical D")
            near(pm["dq"],pfm["dq"],"incremental/rebuilt physical D")
            cap = em["cost"]  # epsilon=0; numerical tolerances are recorded separately.
            record["economic_reference"] = dict(
                upper=em["cost"],lower=econ["lower_bound"],
                reference_gap=em["cost"]-econ["lower_bound"])
            record["cost_cap"] = cap
            incremental.set_cost_cap(cap)
            lex = phase(incremental,"dq","economic_band_min_dq",phases,cap,3)
            lm = proved(lex,"economic-band repair")
            near(lm["dq"],case["expected"]["dq_economic"],"analytical economic-band D")
            record["reported_physical_dq"],record["reported_economic_dq"] = pm["dq"],lm["dq"]
            record["reported_cost"] = lm["cost"]
            # A valid reference itself witnesses feasibility for full permission.
            require(lex["optimal"],"full-permission reference feasibility invariant")
            internal = case["group"] in ("N1","D1","R1","N4","R4")
            for scope in (0,1):
                incremental.set_scope(scope)
                r = phase(incremental,"dq",f"scope_{scope}",phases,cap,scope)
                if scope == 0:
                    permitted = internal
                elif case["group"] in ("N2","D2","R2"):
                    permitted = case["severity"] < 3
                elif case["group"] in ("N3","D3","R3"):
                    permitted = case["severity"] == 1
                elif case["group"] == "D4":
                    permitted = case["severity"] == 1
                else:
                    permitted = True
                check_expected_status(r,permitted,f"scope K={scope}")
            incremental.set_scope(3)
            if "frontier" in case["expected"]:
                f = case["expected"]["frontier"]
                saturation = (f["y_max"]-f["y_min"])*f["saving"]
                for fraction in (0.5,1.0):
                    eps = fraction*saturation
                    current_cap = cap+eps
                    incremental.set_cost_cap(current_cap)
                    result = phase(incremental,"dq",f"budget_{fraction}",
                                   phases,current_cap,3)
                    vm = proved(result,"budget frontier")
                    y = max(f["y_min"],f["y_max"]-eps/f["saving"])
                    near(vm["dq"],2*f["delta"]+y/0.81,"analytical budget frontier")
            record["classification"] = "VALIDATED_FEASIBLE"
        record["status"] = "PASS"
    except Unresolved as exc:
        record.update(status="UNKNOWN",error=str(exc))
    except Exception as exc:
        record.update(status="FAIL",error=str(exc),traceback=traceback.format_exc())
    record["elapsed_seconds"] = time.perf_counter()-started
    return record


def semantic_checks(cases,records):
    checks = {}
    first = deepcopy(cases[0])
    state,_ = apply_event(first,first["event"])
    # Repeated IDs with different payload and stale versions must not mutate state.
    collision = deepcopy(first["event"])
    collision["edge"] = "B"
    before = deepcopy(state)
    try:
        apply_event(state,collision)
        checks["event_id_collision"] = False
    except ValueError:
        checks["event_id_collision"] = state == before
    stale = deepcopy(first["event"])
    stale["id"] += "-stale"
    try:
        apply_event(state,stale)
        checks["stale_event_version"] = False
    except ValueError:
        checks["stale_event_version"] = state == before
    by_id = {r["case_id"]:r for r in records if r["status"] == "PASS"}
    if "N1-S1" in by_id:
        r = by_id["N1-S1"]
        s,p = r["state"],r["phases"]["economic"]["plan"]
        bad = deepcopy(p)
        bad["tasks"]["new"][3] = 0.25
        checks["checker_rejects_executed_prefix"] = not verify(s,bad)["valid"]
        bad = deepcopy(p)
        bad["grid"]["buy"][4] += 0.1
        checks["checker_rejects_bus_violation"] = not verify(s,bad)["valid"]
        bad = deepcopy(p)
        bad["version"] -= 1
        checks["checker_rejects_stale_plan"] = not verify(s,bad)["valid"]
    if "N2-S1" in by_id:
        r = by_id["N2-S1"]
        s,p = r["state"],r["phases"]["economic"]["plan"]
        bad = deepcopy(p)
        bad["resources"]["b_ev"]["charge"][5] = 0.1
        checks["checker_rejects_after_departure"] = not verify(s,bad)["valid"]
        checks["checker_rejects_scope_violation"] = not verify(s,p,participant_cap=0)["valid"]
        checks["checker_rejects_cost_violation"] = not verify(
            s,p,cost_cap=verify(s,p)["cost"]-1e-3)["valid"]
        idle = deepcopy(s["reference"])
        # On original state, meaningless idle mode flips must not create participation.
        pre = next(c for c in cases if c["id"] == "N2-S1")
        idle["resources"]["b_ev"]["mode"][8] = 1.0
        iv = verify(pre,idle)
        checks["idle_modes_not_counted_as_adjustments"] = iv["valid"] and iv["n_adjust"] == 0
    if "R1-S2" in by_id:
        r = by_id["R1-S2"]
        bad = deepcopy(r["phases"]["economic"]["plan"])
        bad["resources"]["a_store"]["energy"][7] += 1
        checks["checker_rejects_state_corruption"] = not verify(r["state"],bad)["valid"]
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate",action="store_true",help="Create the 36 synthetic JSON inputs.")
    parser.add_argument("--only",help="Comma-separated case IDs.")
    parser.add_argument("--output",default="results/basic")
    args = parser.parse_args()
    if args.generate:
        generate()
    paths = sorted((ROOT/"datasets"/"basic").glob("*.json"))
    require(bool(paths),"No dataset. Run with --generate to create the authorized fixtures.")
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
        require(len(cases) == len(wanted),"Unknown case ID")
    out = ROOT/args.output
    out.mkdir(parents=True,exist_ok=True)
    started,records = time.perf_counter(),[]
    for case in cases:
        record = validate_case(case)
        records.append(record)
        write_json(out/"cases"/(case["id"]+".json"),record)
        print(f"{case['id']}: {record['status']} "+
              record.get("error",record.get("classification","")),flush=True)
    semantics = semantic_checks(cases,records)
    passed = sum(r["status"] == "PASS" for r in records)
    summary = dict(purpose="Correctness only; no empirical performance/speed claim",
                   python=sys.version,platform=platform.platform(),
                   highs=highspy.Highs().version(),numpy=numpy.__version__,
                   count=len(records),passed=passed,
                   failed=sum(r["status"] == "FAIL" for r in records),
                   unknown=sum(r["status"] == "UNKNOWN" for r in records),
                   expected_physical_infeasible=sum(not c["expected"]["feasible"] for c in cases),
                   semantics=semantics,elapsed_seconds=time.perf_counter()-started,
                   all_passed=passed == len(records) and all(semantics.values()),
                   cases=[dict(case_id=r["case_id"],status=r["status"],
                               error=r.get("error"),classification=r.get("classification"))
                          for r in records])
    write_json(out/"summary.json",summary)
    with (out/"case_results.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        names = ["case_id","group","status","classification","reported_cost",
                 "reported_physical_dq","reported_economic_dq","elapsed_seconds","error"]
        writer = csv.DictWriter(stream,fieldnames=names,extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k:r.get(k) for k in names})
    lines = ["# 路线 A：基础正确性核验","",
             "本报告不是性能实验，不证明速度或效果优势。","",
             f"- 核验实例：{len(records)}",
             f"- 通过：{passed}",
             f"- 未通过：{summary['failed']}",
             f"- 尚未确定：{summary['unknown']}",
             f"- 预期物理不可行：{summary['expected_physical_infeasible']}",
             f"- HiGHS：{summary['highs']}","",
             "|实例|结果|分类|物理最小修改/kWh|经济最优带内修改/kWh|",
             "|---|---|---|---:|---:|"]
    for r in records:
        lines.append(f"|{r['case_id']}|{r['status']}|{r.get('classification','')}|"
                     f"{r.get('reported_physical_dq','—')}|"
                     f"{r.get('reported_economic_dq','—')}|")
    lines += ["","## 语义与独立核验器检查",""]
    lines += [f"- {name}: {'PASS' if ok else 'FAIL'}" for name,ok in semantics.items()]
    lines += ["","## 边界","",
              "增量更新复用同一个 HiGHS 对象；不声称复用分支搜索树。",
              "本次仅核验确定性模型与解析实例；没有实施后续能力择优、连续事件效果对比或正式性能实验。",
              "所有经济上限均记录实际值；数值容差独立于经济预算。",
              "逐例 JSON 包含原计划、更新后的状态、实际增量矩阵审核、完整解与独立物理残差。"]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:summary[k] for k in ("count","passed","failed","unknown","all_passed")},
                     ensure_ascii=False),flush=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
