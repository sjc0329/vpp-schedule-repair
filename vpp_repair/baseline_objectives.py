"""Independent objective evaluation for explicitly adapted literature mechanisms.

    No optimization package is imported here. Tracking is a mean of squared,
    nameplate-normalized deviations. New tasks have hard requirements but no
    fabricated pre-event tracking reference.
"""
import math


def tracking_terms(state):
    """Describe optimization terms; evaluation below independently reads plans."""
    from .model import key
    ref = state["reference"]
    start,n = state["prefix"],state["horizon"]
    power,energy = [],[]
    for r in state["resources"]:
        rid = r["id"]
        for kind,field,scale in (("c","charge",r["charge_nameplate"]),
                                 ("d","discharge",r["discharge_nameplate"])):
            if scale>0:
                for k in range(max(start,r.get("arrival",0)),min(n,r["departure"])):
                    power.append(dict(variable=key(kind,rid,k),
                        reference=ref["resources"][rid][field][k],scale=scale))
        for k in range(max(start+1,r.get("arrival",0)),min(n,r["departure"])+1):
            energy.append(dict(variable=key("E",rid,k),
                reference=ref["resources"][rid]["energy"][k],scale=r["capacity"]))
    for edge in state["load"]:
        scale = max((state["pv"][edge][k]+state["wind"][edge][k] for k in range(start,n)),default=0)
        if scale>0:
            for k in range(start,n):
                power.append(dict(variable=key("r",edge,k),
                    reference=ref["edges"][edge]["renewable"][k],scale=scale))
    for task in state["tasks"]:
        if task["id"] in ref["tasks"] and task["pmax"]>0:
            for k in range(max(start,task["arrival"]),min(n,task["deadline"])):
                power.append(dict(variable=key("u",task["id"],k),
                    reference=ref["tasks"][task["id"]][k],scale=task["pmax"]))
    return dict(power=power,energy=energy)


def evaluate_tracking(state,plan):
    ref = state["reference"]
    start,n = state["prefix"],state["horizon"]
    power,energy = [],[]
    for r in state["resources"]:
        actual,old = plan["resources"][r["id"]],ref["resources"][r["id"]]
        for field,scale in (("charge",r["charge_nameplate"]),("discharge",r["discharge_nameplate"])):
            if scale>0:
                power.extend(((actual[field][k]-old[field][k])/scale)**2
                    for k in range(max(start,r.get("arrival",0)),min(n,r["departure"])))
        energy.extend(((actual["energy"][k]-old["energy"][k])/r["capacity"])**2
            for k in range(max(start+1,r.get("arrival",0)),min(n,r["departure"])+1))
    for edge,actions in plan["edges"].items():
        available = [state["pv"][edge][k]+state["wind"][edge][k] for k in range(start,n)]
        scale = max(available,default=0)
        if scale>0:
            power.extend(((actions["renewable"][k]-ref["edges"][edge]["renewable"][k])/scale)**2
                         for k in range(start,n))
    for task in state["tasks"]:
        tid = task["id"]
        if tid in ref["tasks"] and task["pmax"]>0:
            power.extend(((plan["tasks"][tid][k]-ref["tasks"][tid][k])/task["pmax"])**2
                for k in range(max(start,task["arrival"]),min(n,task["deadline"])))
    return dict(power_squared=sum(power)/len(power) if power else 0.0,
                energy_squared=sum(energy)/len(energy) if energy else 0.0,
                power_terms=len(power),energy_terms=len(energy))


def tracking_objective(state,plan,cost,cost_scale,penalty,rho):
    if not (math.isfinite(cost_scale) and cost_scale>0 and math.isfinite(penalty)
            and penalty>=0 and 0<=rho<=1):
        raise ValueError("invalid tracking parameters")
    values = evaluate_tracking(state,plan)
    values["objective"] = cost/cost_scale+penalty*(rho*values["power_squared"]+
                                                 (1-rho)*values["energy_squared"])
    return values


def scalar_objective(validation,normalization,weights):
    if (set(weights)!={"cost","deviation","participation"} or
            any(not math.isfinite(w) or w<0 for w in weights.values()) or
            abs(sum(weights.values())-1)>1e-10):
        raise ValueError("weights must be nonnegative and sum to one")
    raw = dict(cost=validation["cost"],deviation=validation["d_vpp"],
               participation=validation["n_adjust"])
    normalized = {}
    for field,value in raw.items():
        item = normalization[field]
        if not math.isfinite(item["scale"]) or item["scale"]<=0:
            raise ValueError("normalization scales must be positive")
        normalized[field] = (value-item["ideal"])/item["scale"]
    return dict(raw=raw,normalized=normalized,
                objective=sum(weights[k]*normalized[k] for k in raw))
