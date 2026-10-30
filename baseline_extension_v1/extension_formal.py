"""Frozen formal comparison; no original evidence is modified."""
from pathlib import Path
import csv,json,time
import extension_prevalidate as a

ROOT=a.ROOT
OUT=Path(__file__).resolve().parent/'formal_results'

def main():
    assert not OUT.exists()
    sources=[]
    for stage in ('main_stage1_v1','main_stage2_v1'):
        for p in sorted((ROOT/'datasets'/stage).glob('*-event-state.json')):
            name=p.name.removesuffix('-event-state.json')
            sources.append(dict(stage=stage,case_id=name,input_path=str(p.relative_to(ROOT)),
                 own_path=str(Path('results')/stage/'cases'/(name+'.json'))))
    assert len(sources)==60
    before=a.protected()
    a.write(OUT/'protocol.json',dict(cases=sources,parameters=dict(segan_lambda=8,
           settlement_calibrated=[1.15,.9],settlement_source=[1.3,.8]),
           alpha=[0,.1,.3],maximum_solver_calls=720,per_solve_seconds=20,
           formal_results_used_for_parameter_selection=False,protected_sha256=before,
           runner_sha256=a.sha(Path(__file__)),adapter_sha256=a.sha(Path(a.__file__)),
           metrics='Physical D, C-U, actual N at 2e-7 and 0.01 kW, internal modification',
           timing='All solver calls summed; not an equal total CPU-budget benchmark'))
    rows=[];calls=0
    for item in sources:
        s=a.read(ROOT/item['input_path']);old=a.read(ROOT/item['own_path'])
        econ=old['configurations']['economic']['detail'];assert econ['optimal']
        ev=a.checked(s,econ['plan'],None);u=ev['cost'];energy=s['event']['task']['energy']
        rec=dict(source=item,economic_reference=dict(cost=u,validation=ev),runs={})
        for label,alpha in [('alpha_0',0.),('alpha_0p1',.1),('alpha_0p3',.3)]:
            cap=u+alpha*.8*energy
            own=old['configurations'][label]['detail']
            ownv=a.checked(s,own['selected_plan'],cap)
            rec['runs'][label]={}
            for method in ('segan','settlement_calibrated','settlement_source'):
                m=a.RepairModel(s);m.set_cost_cap(cap)
                if method=='segan':
                    r0=a.solve(m,a.weighted(m,8));calls+=1
                    assert r0['plan'] is not None,'No SEGAN incumbent'
                    v0=a.checked(s,r0['plan'],cap)
                    assert abs(r0['objective']-v0['cost']-8*v0['d_vpp'])<2e-5
                    scores=a.scores(s,r0['plan']);edge=sorted(scores,key=lambda e:(-scores[e],e))[0]
                    m.bounds('participate|'+edge,0,0);m.set_total_cap(v0['d_vpp'])
                    r1=a.solve(m,a.weighted(m,8));calls+=1
                    plan,v,accepted=r0['plan'],v0,False
                    if r1['plan'] is not None:
                        plan=r1['plan'];v=a.checked(s,plan,cap,v0['d_vpp'],edge);accepted=True
                        assert abs(r1['objective']-v['cost']-8*v['d_vpp'])<2e-5
                    detail=dict(first_pass=r0,backup_pass=r1,scores=scores,candidate=edge,
                         backup_accepted=accepted,selected_plan=plan,validation=v)
                    elapsed=r0['solver_seconds']+r1['solver_seconds'];ncalls=2
                    status=r0['status'];optimal=r0['optimal'];backup=r1['status']
                else:
                    bm,bp=(1.15,.9) if method=='settlement_calibrated' else (1.3,.8)
                    r=a.solve(m,a.settlement_terms(m,bm,bp));calls+=1
                    assert r['plan'] is not None,'No settlement incumbent'
                    plan=r['plan'];v=a.checked(s,plan,cap)
                    obj=a.settlement_value(s,plan,bm,bp)
                    assert abs(obj-r['objective'])<2e-5
                    detail=dict(solve=r,beta_minus=bm,beta_plus=bp,selected_plan=plan,validation=v,independent_objective=obj)
                    elapsed=r['solver_seconds'];ncalls=1;status=r['status'];optimal=r['optimal'];backup=''
                assert v['d_vpp']+2e-7>=ownv['d_vpp'],'Baseline beats recorded global preservation optimum: audit required'
                rec['runs'][label][method]=detail
                rows.append(dict(case_id=s['id'],family=old['family'],alpha=alpha,method=method,
                    cost_cap=cap,reference_cost=u,d=v['d_vpp'],cost=v['cost'],extra=v['cost']-u,
                    n=v['n_adjust'],n_engineering=v['n_engineering'],d_edge=v['d_edge'],
                    own_d=ownv['d_vpp'],own_cost=ownv['cost'],own_extra=ownv['cost']-u,
                    own_n=ownv['n_adjust'],own_n_engineering=ownv['n_engineering'],own_d_edge=ownv['d_edge'],
                    event_energy=energy,status=status,first_pass_optimal=optimal,backup_status=backup,
                    solver_calls=ncalls,solver_seconds=elapsed))
                a.write(OUT/'progress.json',dict(completed=len(rows),planned=540,calls=calls,case=s['id'],alpha=alpha,method=method))
            a.write(OUT/'cases'/(s['id']+'.json'),rec)
        with (OUT/'metrics.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        print(f'{s["id"]}: {len(rows)}/540 outputs, {calls}/720 calls',flush=True)
    assert len(rows)==540 and calls==720
    changed=[k for k,v in before.items() if a.sha(ROOT/k)!=v]
    a.write(OUT/'preservation.json',dict(valid=not changed,checked=len(before),changed=changed));assert not changed
    summary={}
    for method in ('segan','settlement_calibrated','settlement_source'):
        rr=[r for r in rows if r['method']==method]
        mean=lambda k:sum(r[k] for r in rr)/len(rr)
        summary[method]=dict(pairs=len(rr),first_pass_optimal=sum(r['first_pass_optimal'] for r in rr),
            baseline_d=mean('d'),own_d=mean('own_d'),d_reduction_percent=100*(mean('d')-mean('own_d'))/mean('d') if mean('d') else None,
            baseline_extra=mean('extra'),own_extra=mean('own_extra'),own_minus_baseline_cost=mean('own_cost')-mean('cost'),
            baseline_n=mean('n'),own_n=mean('own_n'),baseline_n_engineering=mean('n_engineering'),own_n_engineering=mean('own_n_engineering'),
            own_d_lower=sum(r['own_d']<r['d']-max(.01,.01*r['event_energy']) for r in rr),
            own_d_close=sum(abs(r['own_d']-r['d'])<=max(.01,.01*r['event_energy']) for r in rr),
            own_d_higher=sum(r['own_d']>r['d']+max(.01,.01*r['event_energy']) for r in rr),
            total_solver_seconds=sum(r['solver_seconds'] for r in rr))
    a.write(OUT/'summary.json',dict(cases=60,outputs=540,solver_calls=720,original_evidence_unchanged=True,comparisons=summary))
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':main()
