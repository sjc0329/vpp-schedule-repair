"""Persistent HiGHS MILP, with semantic IDs and auditable incremental edits."""
from copy import deepcopy
import hashlib
import json
import math
import time

import highspy
import numpy as np

INF = highspy.kHighsInf


def key(kind, owner, k):
    return f"{kind}|{owner}|{k}"


class RepairModel:
    def __init__(self, state):
        self.state = deepcopy(state)
        self.h = highspy.Highs()
        for option, value in (
            ("output_flag", False), ("time_limit", 20.0),
            ("threads", 1), ("random_seed", 0),
            ("mip_rel_gap", 0.0), ("mip_abs_gap", 1e-9),
            ("primal_feasibility_tolerance", 1e-8),
            ("dual_feasibility_tolerance", 1e-8),
            ("mip_feasibility_tolerance", 1e-8)):
            self._check(self.h.setOptionValue(option, value))
        self.variables, self.rows = {}, {}
        self.econ, self.dq, self.d_vpp, self.participation = {}, {}, {}, {}
        self.edits = dict(added_columns=0, added_rows=0,
                          changed_bounds=0, changed_coefficients=0,
                          changed_row_bounds=0)
        self._build()

    @staticmethod
    def _check(result):
        if result == highspy.HighsStatus.kError:
            raise RuntimeError("HiGHS API error")

    def var(self, name, lower=0.0, upper=INF, binary=False, economic=0.0):
        if name in self.variables:
            raise ValueError("duplicate variable "+name)
        idx = self.h.getNumCol()
        self._check(self.h.addVar(float(lower), float(upper)))
        if binary:
            self._check(self.h.changeColIntegrality(idx, highspy.HighsVarType.kInteger))
        self.variables[name] = dict(index=idx, lower=float(lower),
                                    upper=float(upper), binary=binary)
        if economic:
            self.econ[name] = float(economic)
        self.edits["added_columns"] += 1

    def bounds(self, name, lower, upper):
        v = self.variables[name]
        if (float(lower), float(upper)) != (v["lower"], v["upper"]):
            self._check(self.h.changeColBounds(v["index"], float(lower), float(upper)))
            v.update(lower=float(lower), upper=float(upper))
            self.edits["changed_bounds"] += 1

    def row(self, name, coeff, lower=-INF, upper=INF):
        coeff = {k: float(v) for k,v in coeff.items() if v != 0}
        if name not in self.rows:
            idx = self.h.getNumRow()
            cols = np.array([self.variables[v]["index"] for v in coeff], dtype=np.int32)
            vals = np.array(list(coeff.values()), dtype=np.float64)
            self._check(self.h.addRow(float(lower), float(upper), len(cols), cols, vals))
            self.rows[name] = dict(index=idx, coeff=coeff,
                                   lower=float(lower), upper=float(upper))
            self.edits["added_rows"] += 1
            return
        old = self.rows[name]
        if (float(lower),float(upper)) != (old["lower"],old["upper"]):
            self._check(self.h.changeRowBounds(old["index"],float(lower),float(upper)))
            self.edits["changed_row_bounds"] += 1
        for v in old["coeff"].keys() | coeff.keys():
            if old["coeff"].get(v,0) != coeff.get(v,0):
                self._check(self.h.changeCoeff(
                    old["index"], self.variables[v]["index"], coeff.get(v,0)))
                self.edits["changed_coefficients"] += 1
        old.update(coeff=coeff, lower=float(lower), upper=float(upper))

    def _energy_lower(self, r, k):
        return max(r["lower"][k], r["requirements"].get(str(k), 0.0))

    def _build(self):
        s = self.state
        start, n, dt = s["prefix"], s["horizon"], s["dt"]
        for e in s["load"]:
            for k in range(start,n):
                self.var(key("q",e,k), -s["interface_limit"],s["interface_limit"])
                self.var(key("r",e,k), upper=s["pv"][e][k]+s["wind"][e][k])
                self.var(key("abs",e,k))
                self.dq[key("abs",e,k)] = dt
                old = s["reference"]["edges"][e]["q"][k]
                self.row(key("abs_pos",e,k),
                         {key("abs",e,k):1,key("q",e,k):-1},lower=-old)
                self.row(key("abs_neg",e,k),
                         {key("abs",e,k):1,key("q",e,k):1},lower=old)
        for k in range(start,n):
            deviation = key("abs_total","vpp",k)
            self.var(deviation)
            self.d_vpp[deviation] = dt
            old = sum(s["reference"]["edges"][e]["q"][k] for e in s["load"])
            positive = {deviation:1}
            positive.update({key("q",e,k):-1 for e in s["load"]})
            negative = {deviation:1}
            negative.update({key("q",e,k):1 for e in s["load"]})
            self.row(key("total_pos","vpp",k),positive,lower=-old)
            self.row(key("total_neg","vpp",k),negative,lower=old)
        for r in s["resources"]:
            rid = r["id"]
            for k in range(start,n+1):
                self.var(key("E",rid,k),self._energy_lower(r,k),r["capacity"])
            self.row("initial|"+rid, {key("E",rid,start):1},
                     r["initial_energy"],r["initial_energy"])
            for k in range(start,n):
                active = r.get("arrival",0) <= k < r["departure"]
                self.var(key("c",rid,k), upper=r["charge_limit"][k] if active else 0,
                         economic=r["wear"]*dt)
                self.var(key("d",rid,k), upper=r["discharge_limit"][k] if active else 0,
                         economic=r["wear"]*dt)
                self.var(key("mode",rid,k), upper=1,binary=True)
                # Immutable nameplate keeps the matrix stable under bound changes.
                self.row(key("charge_mode",rid,k),
                         {key("c",rid,k):1,
                          key("mode",rid,k):-r["charge_nameplate"]}, upper=0)
                self.row(key("discharge_mode",rid,k),
                         {key("d",rid,k):1,
                          key("mode",rid,k):r["discharge_nameplate"]},
                         upper=r["discharge_nameplate"])
                incoming = (r.get("arrival_energy",0.0)
                            if k+1 == r.get("arrival",0) else 0.0)
                self.row(key("dynamics",rid,k),
                         {key("E",rid,k+1):1,key("E",rid,k):-1,
                          key("c",rid,k):-r["eta_c"]*dt,
                          key("d",rid,k):dt/r["eta_d"]},incoming,incoming)
        for t in s["tasks"]:
            self._add_task(t)
        for k in range(start,n):
            self.var(key("buy","grid",k), upper=s["import_limit"][k],
                     economic=s["buy_price"][k]*dt)
            self.var(key("sell","grid",k), upper=s["export_limit"][k],
                     economic=-s["sell_price"][k]*dt)
            self.var(key("mode","grid",k),upper=1,binary=True)
            self.row(key("buy_mode","grid",k),
                     {key("buy","grid",k):1,
                      key("mode","grid",k):-max(s["import_limit"])},upper=0)
            self.row(key("sell_mode","grid",k),
                     {key("sell","grid",k):1,
                      key("mode","grid",k):max(s["export_limit"])},
                     upper=max(s["export_limit"]))
            coeff = {key("buy","grid",k):1,key("sell","grid",k):-1}
            coeff.update({key("q",e,k):1 for e in s["load"]})
            self.row(key("bus","grid",k),coeff,0,0)
        for e in s["load"]:
            self._edge_balances(e)
        self._scope()

    def _add_task(self, t):
        s = self.state
        for k in range(s["prefix"],s["horizon"]):
            self.var(key("u",t["id"],k),
                     upper=t["pmax"] if t["arrival"] <= k < t["deadline"] else 0)
        self.row("task_energy|"+t["id"],
                 {key("u",t["id"],k):s["dt"]
                  for k in range(s["prefix"],s["horizon"])},
                 t["energy"],t["energy"])

    def _edge_balances(self,e):
        s = self.state
        for k in range(s["prefix"],s["horizon"]):
            coeff = {key("q",e,k):1,key("r",e,k):-1}
            for r in s["resources"]:
                if r["edge"] == e:
                    coeff[key("c",r["id"],k)] = 1
                    coeff[key("d",r["id"],k)] = -1
            for t in s["tasks"]:
                if t["edge"] == e:
                    coeff[key("u",t["id"],k)] = 1
            self.row(key("balance",e,k),coeff,-s["load"][e][k],-s["load"][e][k])

    def _scope(self):
        s, coeff = self.state, {}
        for e in s["load"]:
            if e == s["event"]["edge"]:
                continue
            z = "participate|"+e
            self.var(z,upper=1,binary=True)
            self.participation[z] = 1
            coeff[z] = 1
            controls = []
            for k in range(s["prefix"],s["horizon"]):
                controls.append((key("r",e,k),s["reference"]["edges"][e]["renewable"][k]))
                for r in s["resources"]:
                    if r["edge"] == e:
                        old = s["reference"]["resources"][r["id"]]
                        controls.extend(((key("c",r["id"],k),old["charge"][k]),
                                         (key("d",r["id"],k),old["discharge"][k])))
                for t in s["tasks"]:
                    if t["edge"] == e:
                        old = s["reference"]["tasks"].get(t["id"],[0.0]*s["horizon"])
                        controls.append((key("u",t["id"],k),old[k]))
            for v, old in controls:
                upper = self.variables[v]["upper"]
                big_m = max(abs(old),abs(upper-old))
                if big_m:
                    self.row("scope_upper|"+v,{v:1,z:-big_m},upper=old)
                    self.row("scope_lower|"+v,{v:1,z:big_m},lower=old)
        self.row("scope_limit",coeff,upper=len(coeff))

    def sync(self,new_state):
        if new_state["id"] != self.state["id"] or new_state["prefix"] != self.state["prefix"]:
            raise ValueError("this incremental operation requires the same case/time snapshot")
        old_ids = {t["id"] for t in self.state["tasks"]}
        if not old_ids <= {t["id"] for t in new_state["tasks"]}:
            raise ValueError("task deletion is outside this prototype")
        self.edits = {k:0 for k in self.edits}
        self.state = deepcopy(new_state)
        changed_edges = set()
        for t in new_state["tasks"]:
            if t["id"] not in old_ids:
                if t["edge"] != new_state["event"]["edge"]:
                    raise ValueError("new task must belong to the event Edge")
                self._add_task(t)
                changed_edges.add(t["edge"])
        for r in new_state["resources"]:
            rid = r["id"]
            for k in range(new_state["prefix"],new_state["horizon"]):
                active = r.get("arrival",0) <= k < r["departure"]
                self.bounds(key("c",rid,k),0,r["charge_limit"][k] if active else 0)
                self.bounds(key("d",rid,k),0,r["discharge_limit"][k] if active else 0)
            for k in range(new_state["prefix"],new_state["horizon"]+1):
                self.bounds(key("E",rid,k),self._energy_lower(r,k),r["capacity"])
        for e in changed_edges:
            self._edge_balances(e)
        return deepcopy(self.edits)

    def snapshot(self):
        return dict(version=self.state["version"],prefix=self.state["prefix"],
                    variables={v:{k:a for k,a in data.items() if k != "index"}
                               for v,data in self.variables.items()},
                    rows={r:{k:a for k,a in data.items() if k != "index"}
                          for r,data in self.rows.items()},
                    economic=self.econ, dq=self.dq,
                    d_vpp=self.d_vpp, participation=self.participation)

    def digest(self):
        return hashlib.sha256(json.dumps(self.snapshot(),sort_keys=True).encode()).hexdigest()

    def audit_backend(self):
        """Read back actual backend bounds and coefficients, not only our cache."""
        lp = self.h.getLp()
        problems = []
        inverse = {v["index"]:name for name,v in self.variables.items()}
        if self.h.getNumCol() != len(self.variables) or self.h.getNumRow() != len(self.rows):
            problems.append("backend dimension mismatch")
        for name,v in self.variables.items():
            j = v["index"]
            if (abs(lp.col_lower_[j]-v["lower"]) > 1e-11 or
                    abs(lp.col_upper_[j]-v["upper"]) > 1e-11):
                problems.append("column bounds: "+name)
            expected = highspy.HighsVarType.kInteger if v["binary"] else highspy.HighsVarType.kContinuous
            if lp.integrality_[j] != expected:
                problems.append("column integrality: "+name)
        for name,r in self.rows.items():
            _,lower,upper,_ = self.h.getRow(r["index"])
            _,indices,values = self.h.getRowEntries(r["index"])
            actual = {inverse[int(j)]:float(v) for j,v in zip(indices,values) if v != 0}
            if (abs(lower-r["lower"]) > 1e-11 or abs(upper-r["upper"]) > 1e-11 or
                actual.keys() != r["coeff"].keys() or any(
                    abs(actual[v]-r["coeff"][v]) > 1e-11 for v in actual)):
                problems.append("row: "+name)
        return dict(valid=not problems, errors=problems,
                    columns=len(self.variables),rows=len(self.rows))

    def set_cost_cap(self,value):
        self.row("economic_cap",self.econ,upper=INF if value is None else value)

    def set_scope(self,k):
        self.row("scope_limit",self.rows["scope_limit"]["coeff"],upper=k)

    def set_total_cap(self,value):
        self.row("total_deviation_cap",self.d_vpp,
                 upper=INF if value is None else value)

    def solve(self,objective):
        objectives = dict(economic=self.econ, dq=self.dq,
                          total=self.d_vpp, coordination=self.participation)
        if objective not in objectives:
            raise ValueError("unknown objective: "+str(objective))
        terms = objectives[objective]
        costs = np.zeros(self.h.getNumCol(),dtype=np.float64)
        for v,c in terms.items():
            costs[self.variables[v]["index"]] = c
        self._check(self.h.changeColsCost(len(costs),np.arange(len(costs),dtype=np.int32),costs))
        started = time.perf_counter()
        self._check(self.h.run())
        elapsed = time.perf_counter()-started
        status, info, sol = self.h.getModelStatus(),self.h.getInfo(),self.h.getSolution()
        optimal = status == highspy.HighsModelStatus.kOptimal
        infeasible = status == highspy.HighsModelStatus.kInfeasible
        plan = self._extract(sol.col_value) if sol.value_valid and not infeasible else None
        obj = float(self.h.getObjectiveValue()) if plan is not None else None
        raw_bound = float(info.mip_dual_bound)
        bound = raw_bound if math.isfinite(raw_bound) else None
        return dict(status=self.h.modelStatusToString(status),optimal=optimal,
                    proven_infeasible=infeasible,objective=obj,lower_bound=bound,
                    raw_solver_bound=raw_bound,
                    absolute_gap=(max(0.0,obj-bound)
                                  if obj is not None and bound is not None else None),
                    solver_seconds=elapsed,plan=plan)

    def _extract(self,values):
        s, n = self.state,self.state["horizon"]
        def a(kind,owner):
            out = [0.0]*n
            for k in range(s["prefix"],n):
                out[k] = float(values[self.variables[key(kind,owner,k)]["index"]])
            return out
        p = dict(case_id=s["id"],version=s["version"],
                 edges={e:dict(q=a("q",e),renewable=a("r",e)) for e in s["load"]},
                 resources={},tasks={t["id"]:a("u",t["id"]) for t in s["tasks"]},
                 grid=dict(buy=a("buy","grid"),sell=a("sell","grid"),
                           mode=a("mode","grid")))
        for r in s["resources"]:
            rid = r["id"]
            energy = [None]*(n+1)
            for k in range(s["prefix"],n+1):
                energy[k] = float(values[self.variables[key("E",rid,k)]["index"]])
            p["resources"][rid] = dict(charge=a("c",rid),discharge=a("d",rid),
                                       mode=a("mode",rid),energy=energy)
        # Executed decisions are data, not variables. Preserve their actual
        # nonzero history instead of filling the extracted plan with zeros.
        start,ref = s["prefix"],s["reference"]
        for e,actions in p["edges"].items():
            for field,values in actions.items():
                values[:start] = ref["edges"][e][field][:start]
        for rid,actions in p["resources"].items():
            for field,values in actions.items():
                values[:start] = ref["resources"][rid][field][:start]
        for tid,values in p["tasks"].items():
            values[:start] = ref["tasks"].get(tid,[0.0]*n)[:start]
        for field,values in p["grid"].items():
            values[:start] = ref["grid"][field][:start]
        return p
