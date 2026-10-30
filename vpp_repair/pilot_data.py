"""Frozen nine-case pilot recipe, in kW / kWh / CNY. No outcome tuning."""
from copy import deepcopy
import math

EDGES = ("A","B","C","D")
N, DT, EVENT_SLOT = 96, 0.25, 48


def storage(rid,edge,capacity,power,wear=0.05,arrival=0,departure=N):
    return dict(id=rid,edge=edge,capacity=capacity,
        initial_energy=0.6*capacity if arrival == 0 else 0.0,
        arrival=arrival,arrival_energy=0.6*capacity if arrival else 0.0,
        departure=departure,eta_c=0.95,eta_d=0.95,wear=wear,
        lower=[0.0 if k < arrival else 0.2*capacity for k in range(N+1)],
        requirements={str(departure):0.4*capacity},
        charge_nameplate=power,discharge_nameplate=power,
        charge_limit=[power if arrival <= k < departure else 0.0 for k in range(N)],
        discharge_limit=[power if arrival <= k < departure else 0.0 for k in range(N)])


def empty_reference(case):
    return dict(case_id=case["id"],version=0,
        edges={e:dict(q=[0.0]*N,renewable=[0.0]*N) for e in EDGES},
        resources={r["id"]:dict(charge=[0.0]*N,discharge=[0.0]*N,
            mode=[0.0]*N,energy=[r["initial_energy"]]*(N+1)) for r in case["resources"]},
        tasks={t["id"]:[0.0]*N for t in case["tasks"]},
        grid=dict(buy=[0.0]*N,sell=[0.0]*N,mode=[0.0]*N))


def build_case(family,level):
    name = f"{family}-{level+1}"
    loads = ((6,4,5,4),(10,8,8,6),(12,8,10,8),(14,10,12,10),(8,5,6,5))
    load = {e:[] for e in EDGES}
    pv = {e:[] for e in EDGES}
    wind = {e:[] for e in EDGES}
    price = []
    for k in range(N):
        h = k*DT
        block = 0 if h < 6 else 1 if h < 10 else 2 if h < 16 else 3 if h < 22 else 4
        for j,e in enumerate(EDGES):
            load[e].append(float(loads[block][j]))
            peak = 18 if e == "C" else 12 if e == "D" else 0
            pv[e].append(peak*math.sin(math.pi*(h-6)/12) if 6 <= h < 18 else 0.0)
            wind[e].append(float((4,6,3,5)[int(h//6)]) if e == "D" else 0.0)
        price.append(0.4 if h < 6 or h >= 22 else
                     0.6 if 14 <= h < 16 else 1.0 if 16 <= h < 22 else 0.8)
    event_price = (0.4,0.8,1.2)[level] if family == "ECON" else 0.8
    price[48:50] = [event_price]*2
    departure = (52,60,72)[level] if family == "TIME" else 72
    resources = [storage("a_bess","A",40,10)]
    if family == "SCOPE":
        fractions = ((1,0,0),(0.5,0.5,0),(1/3,1/3,1/3))[level]
        for edge,fraction in zip(("B","C","D"),fractions):
            if fraction:
                resources.append(storage(edge.lower()+"_support",edge,
                                         60*fraction,20*fraction))
    else:
        resources += [storage("b_ev","B",100,20,wear=0.08,
                              arrival=48,departure=departure),
                      storage("c_bess","C",80,20)]
    case = dict(id=name,family=family,level=level+1,horizon=N,prefix=0,dt=DT,
        version=0,interface_limit=40.0,load=load,pv=pv,wind=wind,
        import_limit=[60.0]*N,export_limit=[30.0]*N,buy_price=price,sell_price=[0.2]*N,
        resources=resources,tasks=[
            dict(id="old_a",edge="A",energy=16.0,arrival=56,deadline=72,pmax=8.0),
            dict(id="old_d",edge="D",energy=20.0,arrival=64,deadline=80,pmax=10.0)],
        event_log={})
    case["event"] = dict(id=name+"-event",edge="A",version_from=0,version_to=1,
        type="new_task",task=dict(id="new_a",edge="A",energy=8.0,
                                  arrival=48,deadline=50,pmax=20.0))
    case["reference"] = empty_reference(case)
    return case


def all_pilot_cases():
    return [build_case(family,level) for family in ("ECON","TIME","SCOPE")
            for level in range(3)]


def event_snapshot(case,published):
    snapshot = deepcopy(case)
    snapshot["prefix"] = EVENT_SLOT
    snapshot["reference"] = deepcopy(published)
    for r in snapshot["resources"]:
        r["initial_energy"] = published["resources"][r["id"]]["energy"][EVENT_SLOT]
    for t in snapshot["tasks"]:
        original = t["energy"]
        t["original_energy"] = original
        t["energy"] = original-sum(published["tasks"][t["id"]][:EVENT_SLOT])*DT
        if t["energy"] < -1e-7:
            raise ValueError("executed task energy exceeds total requirement")
        t["energy"] = max(0.0,t["energy"])
    return snapshot
