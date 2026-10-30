"""SCIP adapters over the SAME canonical physical MILP; no binary relaxation.

    Tracking adapts Naughton et al. (2021), equations 42-43; scalarization adapts
    the quality/stability mechanism of Koksalmis et al. (2025), equations 1/9.
    These are explicitly mapped mechanisms, not full original-paper replicas.
"""
import math
import time

from pyscipopt import Model,quicksum

from .baseline_objectives import tracking_terms,tracking_objective,scalar_objective
from .model import RepairModel
from .verify import verify


class ScipBaseline:
    def __init__(self,state,cost_cap=None,time_limit=20.0):
        started = time.perf_counter()
        self.source = RepairModel(state)
        self.state = self.source.state
        if cost_cap is not None:
            self.source.set_cost_cap(cost_cap)
        self.cost_cap = cost_cap
        self.m = Model("VPP-common-feasible-set")
        self.m.hideOutput()
        self.m.setRealParam("limits/time",time_limit)
        self.m.setRealParam("limits/gap",0.0)
        self.m.setRealParam("limits/absgap",1e-9)
        self.m.setRealParam("numerics/feastol",1e-9)
        self.m.setIntParam("parallel/maxnthreads",1)
        self.m.setIntParam("randomization/randomseedshift",0)
        self.variables,self.imported_rows = {},[]
        for name,data in self.source.variables.items():
            self.variables[name] = self.m.addVar(name=name,vtype="B" if data["binary"] else "C",
                lb=None if math.isinf(data["lower"]) else data["lower"],
                ub=None if math.isinf(data["upper"]) else data["upper"])
        for name,row in self.source.rows.items():
            expr = self.linear(row["coeff"])
            lo,hi = row["lower"],row["upper"]
            if lo==hi:
                cons = self.m.addCons(expr==lo,name=name+"::eq")
                self.imported_rows.append((cons,row["coeff"],lo,hi))
            else:
                if math.isfinite(lo):
                    cons = self.m.addCons(expr>=lo,name=name+"::lo")
                    self.imported_rows.append((cons,row["coeff"],lo,math.inf))
                if math.isfinite(hi):
                    cons = self.m.addCons(expr<=hi,name=name+"::hi")
                    self.imported_rows.append((cons,row["coeff"],-math.inf,hi))
        self.import_audit = self.audit_import()
        if not self.import_audit["valid"]:
            raise ValueError("SCIP physical-model import mismatch")
        self.build_seconds = time.perf_counter()-started
        self.offset = 0.0
        self.config = {}
        self.configured = False

    def linear(self,terms):
        return quicksum(value*self.variables[name] for name,value in terms.items())

    def audit_import(self):
        errors = []
        def same(actual,expected):
            return ((actual>=0.99*self.m.infinity() if expected>0 else actual<=-0.99*self.m.infinity())
                    if math.isinf(expected) else abs(actual-expected)<1e-10)
        for name,data in self.source.variables.items():
            variable = self.variables[name]
            if not same(variable.getLbOriginal(),data["lower"]) or not same(variable.getUbOriginal(),data["upper"]):
                errors.append("bounds:"+name)
            if (variable.vtype()=="BINARY") != data["binary"]:
                errors.append("integrality:"+name)
        for cons,coeff,lo,hi in self.imported_rows:
            actual = self.m.getValsLinear(cons)
            if (actual.keys()!=coeff.keys() or any(abs(actual[k]-coeff[k])>1e-10 for k in coeff)
                    or not same(self.m.getLhs(cons),lo) or not same(self.m.getRhs(cons),hi)):
                errors.append("row:"+cons.name)
        return dict(valid=not errors,errors=errors,canonical_digest=self.source.digest(),
                    source_backend_audit=self.source.audit_backend(),
                    imported_variables=len(self.variables),imported_linear_rows=len(self.imported_rows),
                    integer_variables=sum(v["binary"] for v in self.source.variables.values()))

    def _start_objective(self,kind):
        if self.configured:
            raise ValueError("use a fresh adapter for each objective; no stale penalties")
        self.configured = True
        self.config["kind"] = kind

    def set_linear_objective(self,metric):
        self._start_objective("linear")
        objectives = dict(cost=self.source.econ,deviation=self.source.d_vpp,
                          participation=self.source.participation)
        if metric not in objectives:
            raise ValueError("unknown linear metric")
        self.config["metric"] = metric
        self.m.setObjective(self.linear(objectives[metric]),"minimize")

    def set_tracking_objective(self,cost_scale,penalty,rho):
        # Validate before creating any quadratic epigraph.
        if not(math.isfinite(cost_scale) and cost_scale>0 and math.isfinite(penalty)
               and penalty>=0 and math.isfinite(rho) and 0<=rho<=1):
            raise ValueError("invalid tracking parameters")
        self._start_objective("tracking")
        terms = tracking_terms(self.state)
        quadratic = 0.0
        for group,weight in (("power",rho),("energy",1-rho)):
            items = terms[group]
            if items and weight and penalty:
                quadratic += penalty*weight/len(items)*quicksum(
                    ((self.variables[item["variable"]]-item["reference"])/item["scale"])**2
                    for item in items)
        objective = self.linear(self.source.econ)/cost_scale
        if penalty and any(terms.values()):
            epigraph = self.m.addVar("tracking_quadratic_epigraph",lb=0)
            self.m.addCons(epigraph>=quadratic,name="tracking_convex_quadratic")
            objective += epigraph
        self.m.setObjective(objective,"minimize")
        self.config.update(cost_scale=cost_scale,penalty=penalty,rho=rho,
            power_terms=len(terms["power"]),energy_terms=len(terms["energy"]),
            integer_modes_relaxed=False,quadratic_approximation=False)

    def set_scalar_objective(self,normalization,weights):
        scalar_objective(dict(cost=0,d_vpp=0,n_adjust=0),normalization,weights)
        self._start_objective("scalar")
        expressions = dict(cost=self.source.econ,deviation=self.source.d_vpp,
                           participation=self.source.participation)
        self.offset = -sum(weights[k]*normalization[k]["ideal"]/normalization[k]["scale"] for k in weights)
        self.m.setObjective(quicksum(weights[k]/normalization[k]["scale"]*
                                     self.linear(expressions[k]) for k in weights),"minimize")
        self.config.update(normalization=normalization,weights=weights)

    def solve(self):
        if not self.configured:
            raise ValueError("objective not configured")
        started = time.perf_counter()
        self.m.optimize()
        elapsed = time.perf_counter()-started
        status = str(self.m.getStatus())
        best = self.m.getBestSol()
        objective = self.m.getSolObjVal(best)+self.offset if best is not None else None
        raw_bound = self.m.getDualbound()
        lower = raw_bound+self.offset if abs(raw_bound)<0.99*self.m.infinity() else None
        plan,validation,independent = None,None,None
        if best is not None:
            values = [0.0]*len(self.source.variables)
            for name,data in self.source.variables.items():
                values[data["index"]] = self.m.getSolVal(best,self.variables[name])
            plan = self.source._extract(values)
            validation = verify(self.state,plan,cost_cap=self.cost_cap)
            if not validation["valid"]:
                raise ValueError("SCIP returned physically invalid plan: "+str(validation["errors"]))
            if self.config["kind"]=="tracking":
                independent = tracking_objective(self.state,plan,validation["cost"],
                    self.config["cost_scale"],self.config["penalty"],self.config["rho"])
            elif self.config["kind"]=="scalar":
                independent = scalar_objective(validation,self.config["normalization"],self.config["weights"])
            else:
                metric = self.config["metric"]
                independent = dict(objective=validation[{"cost":"cost","deviation":"d_vpp",
                                                         "participation":"n_adjust"}[metric]])
        return dict(status=status,optimal=status=="optimal",proven_infeasible=status=="infeasible",
            objective=objective,lower_bound=lower,
            absolute_gap=max(0,objective-lower) if objective is not None and lower is not None else None,
            objective_recalculation_error=(abs(objective-independent["objective"])
                                          if independent is not None else None),
            configuration=self.config,import_audit=self.import_audit,build_seconds=self.build_seconds,
            solver_seconds=elapsed,plan=plan,validation=validation,independent_objective=independent)


def build_payoff_normalization(state,cost_scale=1.0,cost_cap=None):
    if not math.isfinite(cost_scale) or cost_scale<=0:
        raise ValueError("cost scale must be positive")
    points = {}
    for metric in ("cost","deviation","participation"):
        model = ScipBaseline(state,cost_cap=cost_cap)
        model.set_linear_objective(metric)
        result = model.solve()
        if not result["optimal"] or result["validation"] is None:
            raise ValueError("payoff anchor not proved optimal: "+metric)
        points[metric] = result
    raw = {name:dict(cost=p["validation"]["cost"],deviation=p["validation"]["d_vpp"],
                    participation=p["validation"]["n_adjust"]) for name,p in points.items()}
    task_energy = state["event"].get("task",{}).get("energy",1.0)
    fallback = dict(cost=cost_scale,deviation=max(1.0,task_energy),
                    participation=max(1,len(state["load"])-1))
    normalization = {}
    for metric in raw:
        ideal = raw[metric][metric]
        observed_max = max(point[metric] for point in raw.values())
        span = max(0.0,observed_max-ideal)
        normalization[metric] = dict(ideal=ideal,observed_anchor_max=observed_max,
            scale=span if span>1e-8 else fallback[metric],fallback_used=span<=1e-8,
            observed_max_is_global_bound=False)
    return dict(normalization=normalization,anchors=points,anchor_values=raw,
                anchor_solver_calls=3)
