"""Three objective-module ablations; physical MILP and budget are unchanged."""
import math

from .model import RepairModel
from .repair import select_repair,refine_cost
from .verify import verify


def solve_ablation(state,variant,epsilon=0.0,shared_economic=None):
    variants = ("full","without_plan","without_coordination","without_final_cost")
    if variant not in variants or not math.isfinite(epsilon) or epsilon<0:
        raise ValueError("invalid ablation configuration")
    model = RepairModel(state)
    physical_digest = model.digest()
    if variant=="full":
        result = select_repair(model,epsilon=epsilon,shared_economic=shared_economic)
        result.update(variant=variant,initial_physical_digest=physical_digest)
        return result
    phases = {}
    def phase(label,objective,cost_cap=None,total_cap=None,scope=None):
        result = model.solve(objective)
        if result["plan"] is not None:
            result["validation"] = verify(state,result["plan"],cost_cap,scope,total_cap)
            if not result["validation"]["valid"]:
                raise ValueError("ablation returned invalid plan: "+label)
        phases[label] = result
        return result
    if shared_economic is None:
        economic = phase("economic","economic")
    else:
        economic = dict(shared_economic)
        if economic.get("plan") is None:
            raise ValueError("shared economic reference requires a primal plan")
        economic["validation"] = verify(state,economic["plan"])
        if not economic["validation"]["valid"]:
            raise ValueError("shared reference invalid in ablation state")
        economic["reused_common_reference"] = True
        phases["economic"] = economic
    output = dict(variant=variant,initial_physical_digest=physical_digest,phases=phases,
                  selected_plan=None,selected_validation=None,epsilon=epsilon,
                  declared_objective_order={"without_plan":["participation","cost"],
                    "without_coordination":["total","cost"],
                    "without_final_cost":["total","participation"]}[variant])
    if economic.get("plan") is None:
        output["status"] = "PHYSICALLY_INFEASIBLE" if economic["proven_infeasible"] else "UNKNOWN_REFERENCE"
        return output
    cap = economic["validation"]["cost"]+epsilon
    output["cost_cap"] = cap
    model.set_cost_cap(cap)
    model.set_total_cap(None)
    model.set_scope(len(model.participation))
    total_cap = None
    if variant!="without_plan":
        total = phase("total","total",cap)
        if total["plan"] is None:
            output["status"] = "UNKNOWN_OR_INFEASIBLE_TOTAL_STAGE"
            return output
        total_cap = total["validation"]["d_vpp"]
        model.set_total_cap(total_cap)
        chosen = total
    if variant!="without_coordination":
        coordination = phase("coordination","coordination",cap,total_cap)
        if coordination["proven_infeasible"]:
            raise ValueError("ablation coordination conflicts with verified witness")
        if coordination["plan"] is not None:
            chosen = coordination
        elif variant=="without_plan":
            chosen = economic
    if variant!="without_final_cost":
        scope = (chosen["validation"]["n_adjust"] if variant=="without_plan"
                 else len(model.participation))
        refined = refine_cost(model,chosen["plan"],cap,total_cap,scope)
        phases["cost_tiebreak"] = refined["phase"]
        selected,check = refined["selected_plan"],refined["selected_validation"]
        output["final_scope_cap"] = scope
    else:
        selected,check = chosen["plan"],chosen["validation"]
        output["final_scope_cap"] = len(model.participation)
    output.update(status=("OPTIMAL_WITHIN_RECORDED_BANDS" if all(p["optimal"] for p in phases.values())
                          else "FEASIBLE_WITH_RECORDED_BOUNDS"),
                  total_cap=total_cap,selected_plan=selected,selected_validation=check,
                  physical_constraints_removed=False,
                  economic_reference_upper=economic["validation"]["cost"],
                  economic_reference_lower=economic["lower_bound"])
    return output
