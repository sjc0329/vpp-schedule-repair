"""Small correctness acceptance only; never reads the 60-case evaluation set."""
from copy import deepcopy
import importlib.metadata
import json
from pathlib import Path
import platform
import time

from pyscipopt import Model

from run_pilot import require,write,sha
from run_mechanism_validation import compensation
from vpp_repair.ablations import solve_ablation
from vpp_repair.baseline_objectives import tracking_objective,scalar_objective
from vpp_repair.data import base,make_reference,make_case,apply_event,resource,task
from vpp_repair.model import RepairModel
from vpp_repair.scip_baselines import ScipBaseline,build_payoff_normalization
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/"results"/"baseline_acceptance_v1"
TOL = 2e-6


def near(actual,expected,label):
    require(actual is not None and abs(actual-expected)<TOL,
            f"{label}: got {actual}, expected {expected}")


def accepted(result):
    require(result["optimal"],"not proved optimal: "+result["status"])
    require(result["validation"] is not None and result["validation"]["valid"],"invalid SCIP plan")
    require(result["import_audit"]["valid"] and result["import_audit"]["source_backend_audit"]["valid"],
            "physical importer audit failed")
    require(result["objective_recalculation_error"]<TOL,"independent objective mismatch")


def new_task_state(case,energy=1):
    case["group"] = "CHECK"
    case["event"] = dict(id=case["id"]+"-event",edge="A",type="new_task",
        version_from=0,version_to=1,task=task("new","A",energy,4,5,4*energy))
    case["reference"] = make_reference(case)
    return apply_event(case,case["event"])[0]


def main():
    require(not OUT.exists(),"refusing to overwrite acceptance evidence")
    OUT.mkdir(parents=True)
    core = [ROOT/"vpp_repair"/name for name in ("model.py","repair.py","verify.py","data.py")]
    old = json.loads((ROOT/"datasets/main_stage2_v1/manifest.json").read_text(encoding="utf-8"))
    original_hashes = {k.replace("\\","/"):v for k,v in old["source_sha256"].items()}
    require(all(sha(p)==original_hashes[str(p.relative_to(ROOT)).replace("\\","/")] for p in core),
            "core method changed")
    records = {}
    started = time.perf_counter()

    # Closed form: min ((b/2)^2+(c/2)^2+(d/4)^2)/48,
    # b+c+d=4. Thus b=c=2/3,d=8/3 and objective=1/72.
    state = compensation()
    model = ScipBaseline(state)
    model.set_tracking_objective(cost_scale=1,penalty=1,rho=0.5)
    tracking = model.solve()
    accepted(tracking)
    near(tracking["objective"],1/72,"closed-form power tracking objective")
    for edge,expected in (("B",2/3),("C",2/3),("D",8/3)):
        require(abs(tracking["plan"]["edges"][edge]["renewable"][4]-expected)<2e-3,
                "quadratic resource allocation incorrect")
    require(tracking["configuration"]["power_terms"]==24,"new task entered tracking reference")
    require(tracking["validation"]["n_adjust"]==3,"expected all three quadratic-sharing providers")
    records["quadratic_power_closed_form_and_new_task_exclusion"] = tracking

    storage_case = base("BASE-STATE-TRACKING")
    storage_case["resources"] = [resource("a_store","A",60,discharge={4:4})]
    storage_state = new_task_state(storage_case)
    model = ScipBaseline(storage_state)
    model.set_tracking_objective(1,2,0.25)
    result = model.solve()
    accepted(result)
    near(result["independent_objective"]["power_squared"],1/8,"power mean")
    near(result["independent_objective"]["energy_squared"],1/8100,"energy mean")
    near(result["objective"],0.04+2*(0.25/8+0.75/8100),"power/state exact objective")
    require(result["configuration"]["energy_terms"]==8,"state window incorrect")
    records["quadratic_energy_closed_form"] = result

    economic_crosschecks = []
    for group,severity in (("N2",0),("D3",0),("R1",2)):
        case = make_case(group,severity)
        updated,_ = apply_event(case,case["event"])
        highs = RepairModel(updated).solve("economic")
        baseline = ScipBaseline(updated)
        baseline.set_tracking_objective(1,0,0.5)
        scip = baseline.solve()
        accepted(scip)
        require(highs["optimal"],"HiGHS economic reference not optimal")
        near(scip["validation"]["cost"],verify(updated,highs["plan"])["cost"],"zero-penalty economic equivalence")
        economic_crosschecks.append(dict(case_id=updated["id"],highs=highs,scip=scip))
    records["zero_penalty_same_milp_cross_solver"] = economic_crosschecks

    # Buy/sell binaries are essential: relaxing them would allow a -0.4 cost.
    arbitrage = base("BASE-INTEGER-MODES")
    arbitrage["group"],arbitrage["event"] = "CHECK",dict(edge="A")
    arbitrage["import_limit"][4] = arbitrage["export_limit"][4] = 4
    arbitrage["buy_price"][4],arbitrage["sell_price"][4] = 0.2,1.0
    arbitrage["reference"] = make_reference(arbitrage)
    model = ScipBaseline(arbitrage)
    model.set_tracking_objective(1,0,0.5)
    result = model.solve()
    accepted(result)
    near(result["objective"],0,"binary prevents simultaneous buy/sell")
    near(result["plan"]["grid"]["buy"][4],0,"grid buy")
    near(result["plan"]["grid"]["sell"][4],0,"grid sell")
    require(result["import_audit"]["integer_variables"]>0,"integers were relaxed")
    records["integer_mode_arbitrage_guard"] = result

    case = make_case("R4",2)
    impossible,_ = apply_event(case,case["event"])
    model = ScipBaseline(impossible)
    model.set_linear_objective("cost")
    infeasible = model.solve()
    require(infeasible["proven_infeasible"] and infeasible["plan"] is None,"infeasibility misreported")
    records["same_infeasible_physical_model"] = infeasible

    payoff = build_payoff_normalization(state,cost_scale=1)
    weights = dict(cost=1/3,deviation=1/3,participation=1/3)
    model = ScipBaseline(state)
    model.set_scalar_objective(payoff["normalization"],weights)
    scalar = model.solve()
    accepted(scalar)
    require(scalar["validation"]["n_adjust"]==1,"scalar stability count wrong")
    require(all(item["scale"]>0 for item in payoff["normalization"].values()),"zero normalization divisor")
    require(payoff["normalization"]["cost"]["fallback_used"],"constant cost needs positive fallback")
    require(payoff["normalization"]["deviation"]["fallback_used"],"constant deviation needs positive fallback")
    records["payoff_normalization_and_scalar_objective"] = dict(payoff=payoff,result=scalar)

    negative = scalar_objective(dict(cost=1,d_vpp=2,n_adjust=1),
        dict(cost=dict(ideal=3,scale=2),deviation=dict(ideal=4,scale=2),
             participation=dict(ideal=2,scale=1)),weights)
    near(negative["objective"],-1,"normalized values must not be clipped")
    records["normalization_is_affine_not_clipped"] = negative

    expensive = base("BASE-COORDINATION-ABLATION")
    expensive["pv"]["B"][4] = expensive["pv"]["C"][4] = 2
    expensive["resources"] = [resource("d_store","D",60,discharge={4:4},wear=0.1)]
    changed = new_task_state(expensive)
    common = RepairModel(changed).solve("economic")
    require(common["optimal"],"ablation economic reference missing")
    variants = {name:solve_ablation(changed,name,epsilon=0.2,shared_economic=common)
                for name in ("full","without_plan","without_coordination","without_final_cost")}
    physical = {result["initial_physical_digest"] for result in variants.values()}
    require(len(physical)==1,"ablation changed physical constraints")
    for result in variants.values():
        require(result["selected_validation"]["valid"],"invalid ablation result")
        cap = result.get("cost_cap",result.get("economic_reference",{}).get("cost_cap"))
        require(verify(changed,result["selected_plan"],cost_cap=cap)["valid"],"ablation budget changed")
    require(variants["full"]["selected_validation"]["n_adjust"]==1,"full mechanism should select one provider")
    near(variants["full"]["selected_validation"]["cost"],0.1,"one-provider minimum cost")
    require(variants["without_coordination"]["selected_validation"]["n_adjust"]==2,
            "without coordination should select two free providers")
    near(variants["without_coordination"]["selected_validation"]["cost"],0,"two-provider cost")
    require("coordination" not in variants["without_coordination"]["phases"],"coordination wasn't removed")
    require("total" not in variants["without_plan"]["phases"],"plan priority wasn't removed")
    require("cost_tiebreak" not in variants["without_final_cost"]["phases"],"final cost wasn't removed")
    records["coordination_ablation_and_identical_physics"] = variants

    case = make_case("N2",0)
    case["import_limit"][4],case["buy_price"][4] = 24,0.01
    changed,_ = apply_event(case,case["event"])
    common = RepairModel(changed).solve("economic")
    full = solve_ablation(changed,"full",epsilon=1,shared_economic=common)
    no_plan = solve_ablation(changed,"without_plan",epsilon=1,shared_economic=common)
    near(full["selected_validation"]["d_vpp"],0,"full preservation")
    near(no_plan["selected_validation"]["d_vpp"],2,"without plan preservation")
    require(no_plan["selected_validation"]["n_adjust"]==0,"without-plan coordination first")
    records["plan_priority_ablation_closed_form"] = dict(full=full,without_plan=no_plan)

    invalids = 0
    for call in (lambda:ScipBaseline(state).set_tracking_objective(0,1,0.5),
                 lambda:ScipBaseline(state).set_tracking_objective(1,-1,0.5),
                 lambda:ScipBaseline(state).set_scalar_objective(payoff["normalization"],
                     dict(cost=1,deviation=1,participation=1)),
                 lambda:solve_ablation(state,"unknown"),
                 lambda:solve_ablation(state,"without_plan",epsilon=-1)):
        try:
            call()
        except ValueError:
            invalids += 1
    require(invalids==5,"invalid configuration accepted")
    records["invalid_parameter_rejection"] = dict(rejected=invalids)

    version = Model()
    runtime = dict(python=platform.python_version(),pyscipopt=importlib.metadata.version("pyscipopt"),
        scip=f"{version.getMajorVersion()}.{version.getMinorVersion()}.{version.getTechVersion()}",
        highspy=importlib.metadata.version("highspy"),numpy=importlib.metadata.version("numpy"))
    sources = [ROOT/"vpp_repair"/name for name in ("baseline_objectives.py","scip_baselines.py","ablations.py")]
    summary = dict(all_passed=True,check_groups=len(records),runtime=runtime,
        core_method_unchanged=True,formal_60_case_comparison_executed=False,
        parameter_calibration_executed=False,wall_seconds=time.perf_counter()-started,
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources+[ROOT/"verify_baseline_adapters.py"]})
    write(OUT/"checks.json",dict(summary=summary,checks=records))
    lines = ["# 文献适配基线与消融：小型验收", "",
        f"{len(records)}组检查通过；仅解析/小型输入，未运行60例正式对比或参数校准。", "",
        *[f"- {name}: PASS" for name in records], "",
        "SCIP保留所有原物理变量界、整数性和约束，逐行读取后核对导入；返回计划由原独立核验器检查。",
        "平方项采用精确二次上图约束，没有分段线性近似或放松整数模式。",
        "目标值从原始计划独立重算，平方功率/状态例另有手算值；零惩罚与HiGHS经济解交叉检查。",
        "三种消融仅删去相应目标阶段，物理模型摘要和共同经济预算不变。",
        "归一化收益表参照不是全局最坏界，零跨度采用正尺度，不裁剪归一化值。",
        "这证明实现符合已声明的适配机制，不代表复现原论文全部市场、网络或滚动系统。"]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
