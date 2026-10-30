"""Twelve structural templates for input qualification, not a final test set.

    All parameter choices are explicit before the qualification run. G4 models
    compensated standby providers whose response price exceeds normal energy
    prices; this is a declared targeted operating regime, not a generic battery
    degradation estimate.
"""
import math

from .pilot_data import build_case,empty_reference,storage


def solar(case,edge,peak):
    case["pv"][edge] = [peak*math.sin(math.pi*(k*case["dt"]-6)/12)
                        if 6 <= k*case["dt"] < 18 else 0.0
                        for k in range(case["horizon"])]


def all_formal_templates():
    cases = []
    for family in ("G1","G2","G3","G4"):
        for level in range(3):
            case = build_case("TIME",2)
            case.update(id=f"TPL-{family}-{level+1}",family=family,level=level+1,
                        qualification_only=True)
            design = {}
            if family == "G1":
                delta = (0.0,0.01,0.05)[level]
                case["buy_price"][48:64] = [0.6]*16
                next(r for r in case["resources"] if r["id"]=="c_bess")["wear"] = 0.05+delta
                design = dict(regime="equal_or_near_equal_dispatch_costs",
                    plateau=[48,64],plateau_price=0.6,storage_cost_difference=delta,
                    hypothesis="Economic-band plan preservation can select different equivalent actions")
            elif family == "G2":
                if level == 0:
                    solar(case,"A",80)
                    regime = "local_absorption"
                else:
                    solar(case,"C",100)
                    case["interface_limit"] = 60.0 if level==1 else 40.0
                    case["export_limit"] = [0.0]*case["horizon"]
                    regime = "remote_absorption" if level==1 else "remote_interface_boundary"
                design = dict(regime=regime,
                    hypothesis="Curtailment and reachable interface space must coexist")
            elif family == "G3":
                departure = (52,60,72)[level]
                ev = storage("b_ev","B",100,20,wear=0.08,arrival=48,departure=departure)
                ev["arrival_energy"] = 45.0
                ev["charge_limit"] = [20.0 if 56 <= k < min(64,departure) else 0.0
                                      for k in range(case["horizon"])]
                case["resources"] = [ev if r["id"]=="b_ev" else r for r in case["resources"]]
                design = dict(regime="departure_and_reserved_charging_window",
                    ev_connection_energy=45.0,departure=departure,required_energy=40.0,
                    charging_window=[56,min(64,departure)],
                    charging_window_interpretation="Station has reserved charging supply 14:00-16:00",
                    hypothesis="A real service-energy margin limits immediate support")
            else:
                fractions = ((1,0,0),(0.5,0.5,0),(1/3,1/3,1/3))[level]
                case["resources"] = [storage(edge.lower()+"_standby",edge,
                    100*fraction,20*fraction,wear=1.01)
                    for edge,fraction in zip(("B","C","D"),fractions) if fraction]
                design = dict(regime="compensated_standby_support",
                    power_fractions=fractions,total_capacity=100.0,total_discharge_power=20.0,
                    response_cost_per_kwh=1.01,
                    cost_interpretation="Contracted standby response compensation plus throughput cost",
                    rationale="1.01 exceeds the predeclared maximum energy price 1.0; idle standby is economical",
                    no_local_storage=True,expected_zero_deviation_participants=level+1,
                    event_window_grid_price=0.8,
                    predicted_extra_cost_for_full_support=(1.01-0.8)*8,
                    hypothesis="With identical total support, distribution forces one/two/three providers")
            case["design"] = design
            case["event"]["id"] = case["id"]+"-event"
            case["reference"] = empty_reference(case)
            cases.append(case)
    return cases
