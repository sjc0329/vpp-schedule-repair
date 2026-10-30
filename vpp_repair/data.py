"""Deterministic synthetic specifications. No legacy project/data dependency."""
from copy import deepcopy
import hashlib
import json

T, PREFIX, DT = 12, 4, 0.25
EDGES = ("A", "B", "C", "D")


def zeros(n=T):
    return [0.0] * n


def series(values):
    out = zeros()
    for k, value in values.items():
        out[int(k)] = float(value)
    return out


def resource(rid, edge, initial, discharge=None, charge=None, departure=T,
             required=40.0, wear=0.04):
    cp, dp = series(charge or {}), series(discharge or {})
    return dict(id=rid, edge=edge, initial_energy=initial, capacity=100.0,
                lower=[20.0] * (T + 1), requirements={str(departure): required},
                departure=departure, charge_limit=cp, discharge_limit=dp,
                charge_nameplate=max(cp), discharge_nameplate=max(dp),
                eta_c=0.9, eta_d=0.9, wear=wear)


def task(tid, edge, energy, arrival, deadline, pmax):
    return dict(id=tid, edge=edge, energy=energy, arrival=arrival,
                deadline=deadline, pmax=pmax)


def base(name):
    return dict(id=name, horizon=T, prefix=PREFIX, dt=DT, version=0,
                interface_limit=40.0,
                load={e: zeros() for e in EDGES},
                pv={e: zeros() for e in EDGES},
                wind={e: zeros() for e in EDGES},
                resources=[], tasks=[], import_limit=zeros(),
                export_limit=zeros(), buy_price=[0.6] * T,
                sell_price=zeros(), event_log={})


def renew(case, edge, values):
    case["pv"][edge] = series({k: v * 0.6 for k, v in values.items()})
    case["wind"][edge] = series({k: v * 0.4 for k, v in values.items()})


def support_cost(delta):
    y = min(4.0, delta)
    return 0.08 * (delta - y) + 0.01 * (y + y / 0.81) + 0.04 * y / 0.81


def make_case(group, severity):
    c = base(f"{group}-S{severity + 1}")
    expected = {}
    if group in ("N2", "D2", "R2"):
        c["resources"] += [
            resource("b_ev", "B", 45, {4: 18}, departure=5, wear=0.08),
            resource("c_store", "C", 40, {4: 16}, {7: 20}, departure=8,
                     wear=0.01)]
        c["import_limit"][7], c["buy_price"][7] = 32.0, 0.04
        delta = (2.0, 4.0, 6.0)[severity]
        lo, hi = max(0.0, delta - 4.5), min(4.0, delta)
        a_cost = 0.0 if group == "N2" else 0.05 * (6.0 - delta)
        expected = dict(feasible=True, dq_physical=2 * delta + lo / 0.81,
                        economic_cost=a_cost + support_cost(delta),
                        dq_economic=2 * delta + hi / 0.81,
                        frontier=dict(delta=delta, y_min=lo, y_max=hi,
                                      saving=0.08-0.01-0.05/0.81))
        if group == "N2":
            ev = dict(type="new_task", task=task("new", "A", delta, 4, 5, 24))
        else:
            c["resources"].append(resource("a_store", "A", 60, {4: 24},
                                           wear=0.05))
            c["load"]["A"][4] = 24.0
            if group == "D2":
                ev = dict(type="derating", resource="a_store", slots=[4],
                          limit=(6.0-delta)/DT)
            else:
                ev = dict(type="reserve", resource="a_store",
                          nodes=list(range(5, 13)),
                          lower=60-(6-delta)/0.9)
    elif group in ("N3", "D3", "R3"):
        c["resources"].append(
            resource("b_ev", "B", 45, {4: 32}, {7: 12}, departure=8))
        renew(c, "C", {7: 12})
        c["tasks"].append(task("old_c", "C", 1.0, 7, 8, 4))
        delta = (4.0, 6.0, 6.5)[severity]
        feasible = delta <= 6.12
        recharge = max(0.0, delta-4.5)/0.81
        expected = dict(
            feasible=feasible,
            dq_physical=2*delta+2*recharge if feasible else None,
            dq_economic=2*delta+2*recharge if feasible else None,
            economic_cost=(0.04*(delta+recharge) +
                           (0.02*(8-delta) if group != "N3" else 0))
            if feasible else None)
        if group == "N3":
            ev = dict(type="new_task", task=task("new", "A", delta, 4, 5, 32))
        else:
            c["resources"].append(resource("a_ev", "A", 60, {4: 32},
                                           departure=5, wear=0.02))
            c["load"]["A"][4] = 32
            if group == "D3":
                ev = dict(type="derating", resource="a_ev", slots=[4],
                          limit=(8-delta)/DT)
            else:
                ev = dict(type="reserve", resource="a_ev", nodes=[5],
                          lower=60-(8-delta)/0.9)
    elif group == "N1":
        renew(c, "A", {4: 2, 5: 2})
        c["import_limit"] = series({4: 4, 5: 4})
        w = (0.5, 1.0, 1.25)[severity]
        need = max(0.0, w-1.0)
        ev = dict(type="new_task", task=task("new", "A", w, 4, 6, 4))
        expected = dict(feasible=True, dq_physical=need,
                        dq_economic=need, economic_cost=0.6*need)
    elif group == "D1":
        c["resources"].append(resource("a_store", "A", 60, {4: 4, 5: 4}))
        c["tasks"].append(task("old_a", "A", 1.0, 4, 6, 4))
        ev = dict(type="derating", resource="a_store", slots=[4],
                  limit=(3.0, 2.0, 0.0)[severity])
        expected = dict(feasible=True, dq_physical=0.0,
                        dq_economic=0.0, economic_cost=0.04)
    elif group == "R1":
        c["resources"].append(resource(
            "a_store", "A", 60, charge={k: 6 for k in range(4, 12)}))
        renew(c, "A", {k: 4 for k in range(4, 12)})
        c["import_limit"] = series({k: 2 for k in range(4, 12)})
        target = (60.0, 65.0, 70.0)[severity]
        recharge, imports = (target-60)/0.9, max(0.0, (target-60)/0.9-8)
        ev = dict(type="reserve", resource="a_store", nodes=[12], lower=target)
        expected = dict(feasible=True, dq_physical=imports,
                        dq_economic=imports,
                        economic_cost=0.04*recharge+0.6*imports)
    elif group == "N4":
        c["tasks"].append(task("old_a", "A", 0.5, 4, 6, 4))
        c["import_limit"] = series({4: 4, 5: 4})
        c["buy_price"][5] = 0.3
        w = (0.5, 1.0, 1.5)[severity]
        ev = dict(type="new_task", task=task("new", "A", w, 4, 5, 8))
        expected = dict(feasible=w <= 1, dq_physical=w if w <= 1 else None,
                        dq_economic=w if w <= 1 else None,
                        economic_cost=0.15+0.6*w if w <= 1 else None)
    elif group == "D4":
        c["resources"] += [
            resource("a_ev", "A", 60, {4: 32}, departure=5, wear=0.02),
            resource("b_ev", "B", 45, {4: 18}, departure=5),
            resource("c_ev", "C", 40, {4: 8}, {6: 12}, departure=7,
                     wear=0.01)]
        c["load"]["A"][4] = 32
        c["import_limit"] = series({6: 4, 9: 12})
        c["buy_price"][6] = c["buy_price"][9] = 0.1
        delta = (4.0, 5.31, 6.0)[severity]
        ev = dict(type="derating", resource="a_ev", slots=[4],
                  limit=(8-delta)/DT)
        yc, yb = max(0.0, delta-4.5), min(delta, 4.5)
        recharge = yc/0.81
        feasible = delta <= 5.31
        expected = dict(
            feasible=feasible,
            dq_physical=2*delta+recharge if feasible else None,
            dq_economic=2*delta+recharge if feasible else None,
            economic_cost=(0.02*(8-delta)+0.04*yb+
                           0.01*(yc+recharge)+0.1*recharge)
            if feasible else None)
    elif group == "R4":
        c["resources"].append(resource("a_store", "A", 40, charge={4: 20}))
        c["import_limit"][4] = 24
        target = (42.0, 44.5, 45.0)[severity]
        recharge = (target-40)/0.9
        feasible = recharge <= 5
        ev = dict(type="reserve", resource="a_store",
                  nodes=list(range(5, 13)), lower=target)
        expected = dict(feasible=feasible,
                        dq_physical=recharge if feasible else None,
                        dq_economic=recharge if feasible else None,
                        economic_cost=0.64*recharge if feasible else None)
    else:
        raise ValueError(group)
    c["group"], c["severity"], c["expected"] = group, severity+1, expected
    ev.update(id=c["id"]+"-event", edge="A", version_from=0, version_to=1)
    c["event"] = ev
    c["reference"] = make_reference(c)
    return c


def make_reference(c):
    n = c["horizon"]
    p = dict(case_id=c["id"], version=0,
             edges={e: dict(q=zeros(), renewable=zeros()) for e in EDGES},
             resources={}, tasks={t["id"]: zeros() for t in c["tasks"]},
             grid=dict(buy=zeros(), sell=zeros(), mode=zeros()))
    for r in c["resources"]:
        p["resources"][r["id"]] = dict(
            charge=zeros(), discharge=zeros(), mode=zeros(),
            energy=[r["initial_energy"]] * (n+1))
    g = c["group"]
    if g == "D1":
        p["tasks"]["old_a"][4] = 4
        p["resources"]["a_store"]["discharge"][4] = 4
    if g in ("D2", "R2"):
        p["resources"]["a_store"]["discharge"][4] = 24
    if g in ("D3", "R3", "D4"):
        p["resources"]["a_ev"]["discharge"][4] = 32
    if g in ("N3", "D3", "R3"):
        p["tasks"]["old_c"][7] = 4
        p["edges"]["C"]["renewable"][7] = 4
    if g == "N4":
        p["tasks"]["old_a"][5] = 2
    for r in c["resources"]:
        a = p["resources"][r["id"]]
        for k in range(n):
            a["energy"][k+1] = (a["energy"][k] +
                r["eta_c"]*a["charge"][k]*c["dt"] -
                a["discharge"][k]*c["dt"]/r["eta_d"])
    for e in EDGES:
        for k in range(n):
            q = p["edges"][e]["renewable"][k] - c["load"][e][k]
            q += sum(p["resources"][r["id"]]["discharge"][k] -
                     p["resources"][r["id"]]["charge"][k]
                     for r in c["resources"] if r["edge"] == e)
            q -= sum(p["tasks"][t["id"]][k]
                     for t in c["tasks"] if t["edge"] == e)
            p["edges"][e]["q"][k] = q
    for k in range(n):
        net = -sum(p["edges"][e]["q"][k] for e in EDGES)
        p["grid"]["buy"][k], p["grid"]["sell"][k] = max(0, net), max(0, -net)
        p["grid"]["mode"][k] = float(net > 0)
    return p


def apply_event(state, event):
    """Atomic, versioned copy update; legal-but-infeasible obligations are retained."""
    digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    previous = state["event_log"].get(event["id"])
    if previous:
        if previous != digest:
            raise ValueError("event ID collision")
        return deepcopy(state), False
    if (event["version_from"] != state["version"] or
            event["version_to"] != state["version"]+1):
        raise ValueError("stale or nonconsecutive version")
    new = deepcopy(state)
    typ = event["type"]
    if typ == "new_task":
        t = event["task"]
        if any(old["id"] == t["id"] for old in new["tasks"]):
            raise ValueError("duplicate task ID")
        if (t["edge"] not in EDGES or t["arrival"] < new["prefix"] or
                not t["arrival"] < t["deadline"] <= new["horizon"] or
                t["energy"] < 0 or t["pmax"] < 0):
            raise ValueError("malformed task")
        new["tasks"].append(deepcopy(t))
    elif typ in ("derating", "reserve"):
        r = next(r for r in new["resources"] if r["id"] == event["resource"])
        if typ == "derating":
            for k in event["slots"]:
                if (not new["prefix"] <= k < new["horizon"] or
                        not 0 <= event["limit"] <= r["discharge_limit"][k]):
                    raise ValueError("invalid derating")
                r["discharge_limit"][k] = event["limit"]
        else:
            for k in event["nodes"]:
                if not new["prefix"] <= k <= new["horizon"] or event["lower"] < 0:
                    raise ValueError("invalid reserve node/lower bound")
                r["lower"][k] = max(r["lower"][k], event["lower"])
    else:
        raise ValueError("unsupported event")
    new["event_log"][event["id"]] = digest
    new["version"] += 1
    return new, True


def all_cases():
    return [make_case(g, s) for g in
            ("N1","N2","N3","N4","D1","D2","D3","D4","R1","R2","R3","R4")
            for s in range(3)]
