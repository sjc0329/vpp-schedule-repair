"""Budgeted total-plan repair, followed by coordination selection."""
import math

from .verify import verify


def refine_cost(model, candidate, cost_cap, total_cap, participant_cap):
    """Minimize cost with recorded plan-deviation and actual-participation caps.

    A verified incumbent provides a fallback and an extra cost upper bound.
    This final stage can also be replayed on a saved earlier-stage result.
    """
    prior = verify(model.state,candidate,cost_cap,participant_cap,total_cap)
    if not prior["valid"]:
        raise ValueError("cost refinement requires a valid prior repair")
    effective_cap = min(cost_cap,prior["cost"])
    model.set_cost_cap(effective_cap)
    model.set_total_cap(total_cap)
    model.set_scope(participant_cap)
    result = model.solve("economic")
    if result["proven_infeasible"]:
        raise ValueError("cost refinement infeasible despite verified prior witness")
    selected,check = candidate,prior
    fallback = True
    if result["plan"] is not None:
        validation = verify(model.state,result["plan"],effective_cap,
                            participant_cap,total_cap)
        result["validation"] = validation
        if not validation["valid"]:
            raise ValueError("cost refinement returned invalid plan")
        if validation["cost"] <= prior["cost"]:
            selected,check,fallback = result["plan"],validation,False
    return dict(phase=result,selected_plan=selected,selected_validation=check,
                effective_cost_cap=effective_cap,participant_cap=participant_cap,
                total_cap=total_cap,cost_proven_optimal=result["optimal"],
                fallback_used=fallback,prior_validation=prior)


def select_repair(model, epsilon=0.0, delta_d=0.0, participant_cap=None,
                  shared_economic=None):
    """Owns phase constraints. No claim of optimality after an interrupted solve.

    The economic reference uses all available non-event Edges, even when the
    repair itself has a smaller participation cap. Later phases retain verified
    incumbents rather than discarding a usable repair on timeout.
    """
    if not all(math.isfinite(v) and v >= 0 for v in (epsilon,delta_d)):
        raise ValueError("budgets must be finite and nonnegative")
    available = len(model.participation)
    if participant_cap is None:
        participant_cap = available
    if (isinstance(participant_cap,bool) or not isinstance(participant_cap,int)
            or not 0 <= participant_cap <= available):
        raise ValueError("invalid participation cap")
    phases = {}

    def phase(name, objective, cost_cap=None, total_cap=None, scope=None):
        result = model.solve(objective)
        if result["plan"] is not None:
            result["validation"] = verify(model.state,result["plan"],
                                           cost_cap,scope,total_cap)
            if not result["validation"]["valid"]:
                raise ValueError(name+": independently invalid solver plan")
        phases[name] = result
        return result

    model.set_cost_cap(None)
    model.set_total_cap(None)
    model.set_scope(available)
    if shared_economic is None:
        economic = phase("economic","economic")
    else:
        # A single common reference is shared across all configurations of a case.
        economic = dict(shared_economic)
        if economic.get("plan") is None:
            raise ValueError("shared economic reference requires a feasible plan")
        economic["validation"] = verify(model.state,economic["plan"])
        if not economic["validation"]["valid"]:
            raise ValueError("shared economic reference fails current-state validation")
        economic["reused_common_reference"] = True
        phases["economic"] = economic
    output = dict(phases=phases,selected_plan=None,selected_validation=None,
                  epsilon=epsilon,delta_d=delta_d,participant_cap=participant_cap)
    if economic["plan"] is None:
        output["status"] = ("PHYSICALLY_INFEASIBLE" if economic["proven_infeasible"]
                            else "UNKNOWN_ECONOMIC_REFERENCE")
        return output
    upper = economic["validation"]["cost"]
    lower = economic["lower_bound"]
    reference_gap = max(0.0,upper-lower) if lower is not None else None
    cap = upper+epsilon
    output["economic_reference"] = dict(upper=upper,lower=lower,
        reference_gap=reference_gap,cost_cap=cap,
        extra_cost_bound=epsilon+reference_gap if reference_gap is not None else None)
    model.set_cost_cap(cap)
    model.set_scope(participant_cap)
    total = phase("total","total",cap,scope=participant_cap)
    if total["plan"] is None:
        # The reference can be a fallback only if it also meets the scope cap.
        check = verify(model.state,economic["plan"],cap,participant_cap)
        if not total["proven_infeasible"] and check["valid"]:
            output.update(status="FEASIBLE_REFERENCE_FALLBACK",
                          selected_plan=economic["plan"],selected_validation=check)
        else:
            output["status"] = ("BUDGET_SCOPE_INFEASIBLE" if total["proven_infeasible"]
                                else "UNKNOWN_REPAIR")
        return output
    best = total["validation"]["d_vpp"]
    total_cap = best+delta_d
    output.update(total_incumbent=best,total_lower_bound=total["lower_bound"],
                  total_proven_optimal=total["optimal"],total_cap=total_cap)
    model.set_total_cap(total_cap)
    coordination = phase("coordination","coordination",cap,total_cap,participant_cap)
    if coordination["proven_infeasible"]:
        # The verified total-stage plan is a feasibility witness for this stage.
        raise ValueError("coordination infeasible despite verified prior witness")
    selected = coordination if coordination["plan"] is not None else total
    check = verify(model.state,selected["plan"],cap,participant_cap,total_cap)
    if not check["valid"]:
        raise ValueError("invalid fallback repair")
    final = refine_cost(model,selected["plan"],cap,total_cap,check["n_adjust"])
    phases["cost_tiebreak"] = final["phase"]
    output.update(status=("OPTIMAL_WITHIN_RECORDED_BANDS"
                          if total["optimal"] and coordination["optimal"]
                          and final["cost_proven_optimal"]
                          else "FEASIBLE_WITH_RECORDED_BOUNDS"),
                  selected_plan=final["selected_plan"],
                  selected_validation=final["selected_validation"],
                  coordination_proven_optimal=coordination["optimal"],
                  cost_proven_optimal=final["cost_proven_optimal"],
                  cost_tiebreak_participant_cap=final["participant_cap"],
                  cost_tiebreak_effective_cap=final["effective_cost_cap"],
                  cost_tiebreak_fallback_used=final["fallback_used"],
                  fallback_used=selected is total or final["fallback_used"])
    return output
