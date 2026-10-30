"""Sufficient, constructive renewable-absorption certificate for one new task.

    Existing storage, EV, old tasks and grid exchange are held fixed. Renewable
    dispatch has no cross-time coupling or incremental cost in this model.
    Failure of this certificate is not failure of the complete repair model.
"""
from copy import deepcopy
from itertools import combinations

from .verify import verify


def certify_absorption(state):
    if state["event"]["type"] != "new_task":
        raise ValueError("certificate supports a single new divisible task")
    task = state["event"]["task"]
    owner,tid = task["edge"],task["id"]
    reference = state["reference"]
    if tid in reference["tasks"]:
        raise ValueError("event task already present in published plan")
    old_state = deepcopy(state)
    old_state["tasks"] = [t for t in state["tasks"] if t["id"] != tid]
    old_plan = deepcopy(reference)
    old_plan["version"] = state["version"]
    old_check = verify(old_state,old_plan)
    if not old_check["valid"]:
        raise ValueError("certificate requires a feasible pre-event reference")
    dt = state["dt"]
    slots = range(max(state["prefix"],task["arrival"]),task["deadline"])
    edges = sorted(e for e in state["load"] if e != owner)
    surplus,export_room,sink_room = {},{},{}
    for k in slots:
        surplus[k] = {e:max(0.0,state["pv"][e][k]+state["wind"][e][k]
                                -reference["edges"][e]["renewable"][k])
                      for e in state["load"]}
        export_room[k] = {e:max(0.0,state["interface_limit"]
                                    -reference["edges"][e]["q"][k]) for e in edges}
        sink_room[k] = max(0.0,reference["edges"][owner]["q"][k]+state["interface_limit"])

    def powers(subset):
        return {k:min(task["pmax"],surplus[k][owner]+min(sink_room[k],
                       sum(min(surplus[k][e],export_room[k][e]) for e in subset)))
                for k in slots}

    subset_caps = []
    selected = None
    for size in range(len(edges)+1):
        for subset in combinations(edges,size):
            cap = sum(powers(subset).values())*dt
            subset_caps.append(dict(edges=list(subset),energy=cap))
            if selected is None and cap+1e-9 >= task["energy"]:
                selected = subset
    result = dict(event_energy=task["energy"],
        local_only_energy=sum(powers(()).values())*dt,
        absorption_energy=sum(powers(edges).values())*dt,
        max_power_by_slot={str(k):v for k,v in powers(edges).items()},
        surplus_by_slot={str(k):v for k,v in surplus.items()},
        export_room_by_slot={str(k):v for k,v in export_room.items()},
        sink_room_by_slot={str(k):v for k,v in sink_room.items()},
        subset_capacities=subset_caps,minimum_direct_support_edges=None,
        direct_absorption_sufficient=selected is not None,
        reference_remaining_cost=old_check["cost"],witness_plan=None,
        witness_validation=None,
        scope="Fixed old controls; renewable curtailment recovery only")
    if selected is None:
        return result
    plan = deepcopy(reference)
    plan["version"] = state["version"]
    plan["tasks"][tid] = [0.0]*state["horizon"]
    remaining = task["energy"]
    allocation = []
    for k,power in powers(selected).items():
        use = min(power,max(0.0,remaining)/dt)
        local = min(use,surplus[k][owner])
        remote = use-local
        plan["tasks"][tid][k] = use
        plan["edges"][owner]["renewable"][k] += local
        plan["edges"][owner]["q"][k] -= remote
        deliveries = {}
        for e in selected:
            delivered = min(remote,min(surplus[k][e],export_room[k][e]))
            plan["edges"][e]["renewable"][k] += delivered
            plan["edges"][e]["q"][k] += delivered
            deliveries[e] = delivered
            remote -= delivered
        if remote > 1e-8:
            raise ValueError("certificate allocation exceeded source capacity")
        remaining -= use*dt
        allocation.append(dict(slot=k,task=use,local=local,remote=deliveries))
    if remaining > 1e-8:
        raise ValueError("certificate allocation did not meet task energy")
    validation = verify(state,plan,total_cap=0.0,participant_cap=len(selected))
    if not validation["valid"]:
        raise ValueError("absorption witness failed independent verification: "+str(validation["errors"]))
    if abs(validation["cost"]-old_check["cost"]) > 2e-7:
        raise ValueError("fixed-control certificate changed operating cost")
    result.update(minimum_direct_support_edges=len(selected),
                  selected_direct_support_edges=list(selected),allocation=allocation,
                  witness_plan=plan,witness_validation=validation)
    return result
