"""Independent replay of saved baseline outputs; invokes no solver."""
from pathlib import Path
import csv, hashlib, json, math, sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from vpp_repair.verify import verify
B=Path(__file__).resolve().parent

def read(p):return json.loads(p.read_text(encoding="utf-8-sig"))

def metrics(s,p):
    v=verify(s,p)
    assert v["valid"],v["errors"]
    changes={e:[] for e in s["load"] if e!=s["event"]["edge"]}
    for e,values in changes.items():
        for k in range(s["prefix"],s["horizon"]):
            values.append(abs(p["edges"][e]["renewable"][k]-s["reference"]["edges"][e]["renewable"][k]))
            for r in s["resources"]:
                if r["edge"]==e:
                    for f in ("charge","discharge"):
                        values.append(abs(p["resources"][r["id"]][f][k]-s["reference"]["resources"][r["id"]][f][k]))
            for t in s["tasks"]:
                if t["edge"]==e:
                    ref=s["reference"]["tasks"].get(t["id"],[0.0]*s["horizon"])
                    values.append(abs(p["tasks"][t["id"]][k]-ref[k]))
    maxima={e:max(v,default=0.) for e,v in changes.items()}
    assert v["n_adjust"]==sum(x>2e-7 for x in maxima.values())
    return v,maxima

protocol=read(B/"results/protocol.json")
rows=list(csv.DictReader((B/"results/metrics.csv").open(encoding="utf-8")))
assert len(rows)==156
look={(r["case_id"],float(r["alpha"]),r["method"],float(r["parameter"])):r for r in rows}
assert len(look)==156
counts={"backup_accepted":0,"backup_rejected_infeasible":0,"backup_other_status":0,"n_lower":0,"n_equal":0,"n_higher":0,"unchanged_candidate_accepted":0}
max_objective_error=0.; initial_optimal=0; total_calls=0
for path in sorted((B/"results/cases").glob("*.json")):
    rec=read(path);s=read(ROOT/rec["input_path"]);u=rec["economic_reference"]["cost"]
    assert hashlib.sha256((ROOT/rec["input_path"]).read_bytes()).hexdigest()==rec["input_sha256"]
    for label,run in rec["runs"].items():
        method,astr,kstr=label.split("_");alpha=float(astr[1:]);kappa=float(kstr[1:]);cap=u+alpha*.8*s["event"]["task"]["energy"]
        row=look[(s["id"],alpha,method,kappa)]
        if method=="segan":
            total_calls+=2;r0=run["first_pass"];r1=run["backup_pass"];initial_optimal+=int(r0["optimal"])
            p=run["selected_plan"];v,mx=metrics(s,p);v0,_=metrics(s,r0["plan"])
            assert v["d_vpp"]<=v0["d_vpp"]+2e-7
            ncmp=v["n_adjust"]-v0["n_adjust"]
            counts["n_lower" if ncmp<0 else "n_higher" if ncmp>0 else "n_equal"]+=1
            if run["backup_accepted"]:
                counts["backup_accepted"]+=1
                assert mx[run["candidate"]]<=2e-7
                _,initial_mx=metrics(s,r0["plan"])
                counts["unchanged_candidate_accepted"]+=int(initial_mx[run["candidate"]]<=2e-7)
            elif r1["proven_infeasible"]:counts["backup_rejected_infeasible"]+=1
            else:counts["backup_other_status"]+=1
            for phase in (r0,r1):
                if phase["plan"] is not None:
                    vm,_=metrics(s,phase["plan"])
                    err=abs(vm["cost"]+.8*kappa*vm["d_vpp"]-phase["objective"])
                    max_objective_error=max(max_objective_error,err);assert err<2e-5
        else:
            total_calls+=1;initial_optimal+=int(run["optimal"]);p=run["plan"];v,mx=metrics(s,p)
            premium=0.
            for k in range(s["prefix"],s["horizon"]):
                old=s["reference"]["grid"];a=s["buy_price"][k]*(p["grid"]["buy"][k]-old["buy"][k])-s["sell_price"][k]*(p["grid"]["sell"][k]-old["sell"][k])
                premium+=s["dt"]*(.3*kappa*max(a,0)+.2*kappa*max(-a,0))
            err=abs(v["cost"]+premium-run["objective"])
            max_objective_error=max(max_objective_error,err);assert err<2e-5
        assert v["cost"]<=cap+2e-7
        for field,key in [("cost","cost"),("d_vpp","d_vpp"),("d_edge","d_edge"),("n_strict","n_adjust")]:
            assert abs(float(row[field])-v[key])<1e-9
        assert int(row["n_engineering"])==sum(x>.01 for x in mx.values())
assert total_calls==234
assert not [k for k,v in protocol["protected_sha256"].items() if hashlib.sha256((ROOT/k).read_bytes()).hexdigest()!=v]
assert hashlib.sha256((B/"extension_prevalidate.py").read_bytes()).hexdigest()==protocol["runner_sha256"]
report=dict(cases=13,output_configurations=156,solver_calls_recounted=234,additional_solver_calls=0,
            independently_reverified=True,first_pass_optimal=initial_optimal,max_objective_error=max_objective_error,
            original_files_unchanged=len(protocol["protected_sha256"]),backup_diagnostics=counts)
(B/"audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report,indent=2))
