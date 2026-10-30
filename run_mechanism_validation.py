"""Small analytical checks only; no formal performance experiment/dataset."""
from copy import deepcopy
import argparse
import json
from pathlib import Path

from vpp_repair.data import base, make_reference, make_case, apply_event, task, renew
from vpp_repair.model import RepairModel, key
from vpp_repair.repair import select_repair
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent


def require(condition,message):
    if not condition:
        raise AssertionError(message)


def near(a,b):
    require(abs(a-b) <= 2e-6,f"expected {b}, got {a}")


def compensation():
    case = base("CHECK-COMPENSATION")
    case["group"] = "CHECK"
    renew(case,"B",{4:2})
    renew(case,"C",{4:2})
    renew(case,"D",{4:4})
    case["reference"] = make_reference(case)
    event = dict(id="check",edge="A",version_from=0,version_to=1,
                 type="new_task",task=task("new","A",1,4,5,4))
    case["event"] = event
    state,_ = apply_event(case,event)
    return state


def main():
    records = {}
    state = compensation()
    model = RepairModel(state)
    repair = select_repair(model)
    require(repair["total_proven_optimal"],"unproved total objective")
    require(repair["coordination_proven_optimal"],"unproved coordination objective")
    check = repair["selected_validation"]
    near(check["d_vpp"],0)
    near(check["d_edge"],2)
    require(check["participants"] == ["D"],"expected one capable provider")
    near(repair["phases"]["coordination"]["objective"],check["n_adjust"])
    require(model.audit_backend()["valid"],"backend mismatch")
    require(not verify(state,repair["selected_plan"],participant_cap=0)["valid"],
            "checker accepted excessive participants")
    records["same_time_compensation_and_coordination"] = repair

    # Equivalent signs across time must not cancel. Both plans satisfy one task.
    case = base("CHECK-TIME")
    case["group"] = "CHECK"
    case["tasks"] = [task("old","A",1,4,6,4)]
    case["import_limit"][4] = case["import_limit"][5] = 4
    case["reference"] = make_reference(case)
    ref = case["reference"]
    ref["tasks"]["old"][4] = 4
    ref["edges"]["A"]["q"][4] = -4
    ref["grid"]["buy"][4] = 4
    ref["grid"]["mode"][4] = 1
    case["event"] = dict(edge="A")
    require(verify(case,ref)["valid"],"invalid nonzero reference")
    shifted = deepcopy(ref)
    for values in (shifted["tasks"]["old"],shifted["grid"]["buy"],
                   shifted["grid"]["mode"],shifted["edges"]["A"]["q"]):
        values[5],values[4] = values[4],0
    shifted_check = verify(case,shifted)
    require(shifted_check["valid"],"invalid shifted plan")
    near(shifted_check["d_vpp"],2)
    require(not verify(case,shifted,total_cap=0)["valid"],"temporal cancellation")
    temporal = RepairModel(case)
    temporal.bounds(key("u","old",4),0,0)
    solved = temporal.solve("total")
    require(solved["optimal"],"unproved forced temporal shift")
    near(solved["objective"],2)
    near(verify(case,solved["plan"])["d_vpp"],2)
    records["no_cancellation_across_time"] = dict(
        shifted_validation=shifted_check,forced_shift=solved)

    # Regression for the full-day pilot bug: nonzero executed actions must survive
    # extraction, and completed tasks must carry only remaining energy.
    history = deepcopy(case)
    history["prefix"] = 5
    history["tasks"][0]["energy"] = 0
    history_result = RepairModel(history).solve("total")
    require(history_result["optimal"],"nonzero-history regression unsolved")
    history_check = verify(history,history_result["plan"])
    require(history_check["valid"],"executed nonzero prefix lost")
    near(history_result["plan"]["grid"]["buy"][4],4)
    near(history_result["plan"]["tasks"]["old"][4],4)
    records["nonzero_executed_prefix_and_remaining_task"] = dict(
        result=history_result,validation=history_check)

    # Exogenous EV connection energy is injected once at its arrival node.
    from vpp_repair.pilot_data import storage
    arrival = base("CHECK-ARRIVAL")
    arrival["group"],arrival["prefix"] = "CHECK",0
    ev = storage("arriving_ev","B",100,20,arrival=4,departure=8)
    ev["lower"] = ev["lower"][:13]
    ev["charge_limit"] = ev["charge_limit"][:12]
    ev["discharge_limit"] = ev["discharge_limit"][:12]
    arrival["resources"] = [ev]
    arrival["reference"] = make_reference(arrival)
    arrival["event"] = dict(edge="A")
    incoming = RepairModel(arrival).solve("economic")
    incoming_check = verify(arrival,incoming["plan"])
    require(incoming["optimal"] and incoming_check["valid"],"arrival model invalid")
    near(incoming["plan"]["resources"]["arriving_ev"]["energy"][3],0)
    near(incoming["plan"]["resources"]["arriving_ev"]["energy"][4],60)
    records["ev_arrival_energy_injection"] = dict(result=incoming,validation=incoming_check)

    # Cheap external purchase versus storage support: budget controls the choice.
    case = make_case("N2",0)
    case["import_limit"][4] = 24
    case["buy_price"][4] = 0.01
    state,_ = apply_event(case,case["event"])
    zero = select_repair(RepairModel(state),epsilon=0)
    generous = select_repair(RepairModel(state),epsilon=1)
    near(zero["selected_validation"]["d_vpp"],2)
    near(generous["selected_validation"]["d_vpp"],0)
    near(zero["economic_reference"]["upper"],
         generous["economic_reference"]["upper"])
    require(generous["selected_validation"]["cost"] <=
            generous["economic_reference"]["cost_cap"]+2e-7,"cost cap violated")
    require(generous["cost_proven_optimal"],"final cost not proved")
    # Total preservation requires direct B support: 2kWh at 0.08 CNY/kWh.
    near(generous["selected_validation"]["cost"],0.16)
    require(generous["selected_validation"]["n_adjust"] == 1,"participation lock lost")
    records["budget_tradeoff"] = dict(zero=zero,generous=generous)

    class InterruptedCost(RepairModel):
        """Interrupt the second economic solve, which is the new cost stage."""
        economic_calls = 0

        def solve(self,objective):
            if objective == "economic":
                self.economic_calls += 1
                if self.economic_calls == 2:
                    return dict(status="SIMULATED_INTERRUPTION",optimal=False,
                                proven_infeasible=False,objective=None,lower_bound=None,
                                plan=None,solver_seconds=0)
            return super().solve(objective)

    interrupted_cost = select_repair(InterruptedCost(state),epsilon=1)
    require(interrupted_cost["cost_tiebreak_fallback_used"],"missing cost-stage fallback")
    require(not interrupted_cost["cost_proven_optimal"],"false final cost proof")
    require(interrupted_cost["selected_validation"]["valid"],"invalid cost fallback")
    previous = interrupted_cost["phases"]["coordination"]["validation"]
    current = interrupted_cost["selected_validation"]
    near(previous["cost"],current["cost"])
    near(previous["d_vpp"],current["d_vpp"])
    require(previous["n_adjust"] == current["n_adjust"],"fallback participation changed")
    records["simulated_cost_interruption_fallback"] = interrupted_cost

    restricted = select_repair(RepairModel(compensation()),participant_cap=0)
    require(restricted["status"] == "BUDGET_SCOPE_INFEASIBLE",
            "scope failure misclassified")
    records["restricted_scope_failure"] = restricted

    class InterruptedCoordination(RepairModel):
        """Synthetic interruption: exercises fallback, not real timeout evidence."""
        def solve(self,objective):
            if objective == "coordination":
                return dict(status="SIMULATED_INTERRUPTION",optimal=False,
                            proven_infeasible=False,objective=None,lower_bound=None,
                            plan=None,solver_seconds=0)
            return super().solve(objective)

    fallback = select_repair(InterruptedCoordination(compensation()),delta_d=1e-6)
    require(fallback["fallback_used"],"missing verified prior incumbent")
    require(not fallback["coordination_proven_optimal"],"false optimality claim")
    require(fallback["selected_validation"]["valid"],"invalid fallback")
    records["simulated_interruption_fallback"] = fallback

    class InterruptedTotal(RepairModel):
        def solve(self,objective):
            if objective == "total":
                return dict(status="SIMULATED_INTERRUPTION",optimal=False,
                            proven_infeasible=False,objective=None,lower_bound=None,
                            plan=None,solver_seconds=0)
            return super().solve(objective)

    reference_fallback = select_repair(InterruptedTotal(compensation()))
    require(reference_fallback["status"] == "FEASIBLE_REFERENCE_FALLBACK",
            "missing economic reference fallback")
    require(reference_fallback["selected_validation"]["valid"],"invalid reference fallback")
    records["simulated_total_interruption_fallback"] = reference_fallback

    def clean(value):
        if isinstance(value,dict):
            return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list):
            return [clean(v) for v in value]
        if isinstance(value,float) and not __import__("math").isfinite(value):
            return None
        return value

    parser = argparse.ArgumentParser()
    parser.add_argument("--output",default="results/mechanism")
    args = parser.parse_args()
    out = ROOT/args.output
    out.mkdir(parents=True,exist_ok=True)
    payload = dict(all_passed=True,count=len(records),
                   purpose="Analytical correctness only; interruptions are simulated",
                   checks=records)
    (out/"checks.json").write_text(json.dumps(clean(payload),ensure_ascii=False,
        indent=2,allow_nan=False)+"\n",encoding="utf-8")
    lines = ["# 总计划保持与协调择优：正确性核验", "",
             "仅解析正确性检查，不是正式效果或效率实验。未生成新的正式数据集。", "",
             *[f"- {name}: PASS" for name in records], "",
             "中断检查使用模拟返回状态，不代表真实求解超时的测量结果。",
             "完整方案、阶段状态、求解界和独立核验结果见 checks.json。"]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(dict(all_passed=True,count=len(records),checks=list(records)),
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
