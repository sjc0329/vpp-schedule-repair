"""Two literature-mechanism adaptations on the 13 frozen calibration cases only."""
from pathlib import Path
import csv
import hashlib
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import highspy
import numpy as np
from vpp_repair.model import RepairModel, key
from vpp_repair.verify import verify, P_TOL

OUT = Path(__file__).resolve().parent / 'results'
ALPHAS = (0.1, 0.3)
SEGAN_GRID = (1.0, 10.0, 100.0)
SETTLEMENT_GRID = (0.5, 1.0, 2.0)

def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def protected():
    return {p.relative_to(ROOT).as_posix(): sha(p)
            for folder in ('vpp_repair', 'datasets', 'results', 'segan_manuscript_v1')
            for p in (ROOT/folder).rglob('*')
            if p.is_file() and '__pycache__' not in p.parts}

def extra_metrics(s, p):
    changed = {}
    for e in s['load']:
        if e == s['event']['edge']:
            continue
        values = []
        for k in range(s['prefix'], s['horizon']):
            values.append(abs(p['edges'][e]['renewable'][k] - s['reference']['edges'][e]['renewable'][k]))
            for r in s['resources']:
                if r['edge'] == e:
                    for f in ('charge', 'discharge'):
                        values.append(abs(p['resources'][r['id']][f][k] - s['reference']['resources'][r['id']][f][k]))
            for t in s['tasks']:
                if t['edge'] == e:
                    old = s['reference']['tasks'].get(t['id'], [0.0]*s['horizon'])
                    values.append(abs(p['tasks'][t['id']][k] - old[k]))
        changed[e] = max(values, default=0.0)
    return dict(max_control_changes=changed, n_engineering=sum(v > .01 for v in changed.values()))

def checked(s, p, cap, total=None, frozen=None):
    v = verify(s, p, cost_cap=cap, total_cap=total)
    v.update(extra_metrics(s, p))
    if frozen is not None and v['max_control_changes'][frozen] > P_TOL:
        v['valid'] = False
        v['errors'].append('Frozen Edge control change exceeds common tolerance')
    if not v['valid']:
        raise AssertionError(v['errors'])
    assert v['n_adjust'] == sum(x > P_TOL for x in v['max_control_changes'].values())
    return v

def solve(m, terms):
    costs = np.zeros(m.h.getNumCol(), dtype=np.float64)
    for v, c in terms.items():
        costs[m.variables[v]['index']] = c
    m._check(m.h.changeColsCost(len(costs), np.arange(len(costs), dtype=np.int32), costs))
    assert m.audit_backend()['valid']
    start = time.perf_counter()
    m._check(m.h.run())
    status, info, sol = m.h.getModelStatus(), m.h.getInfo(), m.h.getSolution()
    infeasible = status == highspy.HighsModelStatus.kInfeasible
    plan = m._extract(sol.col_value) if sol.value_valid and not infeasible else None
    bound = float(info.mip_dual_bound)
    return dict(status=m.h.modelStatusToString(status), optimal=status == highspy.HighsModelStatus.kOptimal,
                proven_infeasible=infeasible, plan=plan,
                objective=float(m.h.getObjectiveValue()) if plan else None,
                lower_bound=bound if math.isfinite(bound) else None,
                solver_seconds=time.perf_counter()-start)

def weighted(m, penalty):
    terms = dict(m.econ)
    for v,c in m.d_vpp.items():
        terms[v] = terms.get(v,0) + penalty*c
    return terms

def scores(s, p):
    return {e: s['dt']*sum(r['wear']*sum(p['resources'][r['id']]['charge'][k] +
                 p['resources'][r['id']]['discharge'][k]
                 for k in range(s['prefix'], s['horizon']))
                 for r in s['resources'] if r['edge']==e)
            for e in s['load'] if e != s['event']['edge']}

def settlement_value(s, p, beta_minus, beta_plus):
    physical = verify(s, p)
    assert physical['valid']
    premium = 0.0
    for k in range(s['prefix'], s['horizon']):
        b, sell = p['grid']['buy'][k], p['grid']['sell'][k]
        old = s['reference']['grid']
        current = s['buy_price'][k]*b - s['sell_price'][k]*sell
        prior = s['buy_price'][k]*old['buy'][k] - s['sell_price'][k]*old['sell'][k]
        change = current-prior
        premium += s['dt']*((beta_minus-1)*max(change,0)+(1-beta_plus)*max(-change,0))
    return physical['cost']+premium

def settlement_terms(m, beta_minus, beta_plus):
    s, terms = m.state, dict(m.econ)
    assert beta_minus >= 1 and 0 <= beta_plus <= 1
    for k in range(s['prefix'], s['horizon']):
        assert s['buy_price'][k] >= s['sell_price'][k] >= 0
        old=s['reference']['grid']
        prior=s['buy_price'][k]*old['buy'][k]-s['sell_price'][k]*old['sell'][k]
        pos, neg = key('settlement_pos','grid',k), key('settlement_neg','grid',k)
        m.var(pos); m.var(neg)
        m.row(key('settlement_pos_bound','grid',k),
              {pos:1,key('buy','grid',k):-s['buy_price'][k],key('sell','grid',k):s['sell_price'][k]},lower=-prior)
        m.row(key('settlement_neg_bound','grid',k),
              {neg:1,key('buy','grid',k):s['buy_price'][k],key('sell','grid',k):-s['sell_price'][k]},lower=prior)
        terms[pos]=s['dt']*(beta_minus-1)
        terms[neg]=s['dt']*(1-beta_plus)
    return terms

def row_from(case, alpha, method, parameter, val, u, calls, elapsed, **extra):
    return dict(case_id=case, family=case.rsplit('-',1)[0], alpha=alpha, method=method,
                parameter=parameter, event_energy=8.0, cost=val['cost'], extra_cost=val['cost']-u,
                d_vpp=val['d_vpp'], d_edge=val['d_edge'], n_strict=val['n_adjust'],
                n_engineering=val['n_engineering'], solver_calls=calls,
                solver_seconds=elapsed, **extra)

def main():
    assert not OUT.exists(), 'Refusing to overwrite prior results'
    old=read(ROOT/'results/baseline_calibration_v1/manifest.json')
    before=protected()
    assert len(old['cases'])==13
    protocol=dict(cases=old['cases'], excluded=old['excluded'], alpha_levels=ALPHAS,
                  segan_kappa=SEGAN_GRID, settlement_kappa=SETTLEMENT_GRID,
                  segan_lambda='0.8*kappa CNY/kWh', settlement_coefficients='beta_minus=1+0.3*kappa; beta_plus=1-0.2*kappa',
                  source_coefficients=dict(beta_minus=1.3,beta_plus=.8), common_time_limit_seconds=20,
                  maximum_solver_calls=247, new_physical_cases=0, formal_cases_used=0,
                  tie_rule='highest adjustable-resource operating cost; lexical Edge id on exact ties',
                  backup_rule='at most one non-event Edge; fix participate flag to zero; retain initial D cap; fallback to first pass',
                  selection_rule='complete coverage; family-balanced D/E, actual N/non-event count, extra cost/(0.8E); parameter value',
                  caution='Core-mechanism adaptations; neither original full control system is reproduced. No total CPU-budget equivalence claimed.',
                  protected_sha256=before, runner_sha256=sha(Path(__file__)))
    write(OUT/'protocol.json',protocol)
    rows=[]; calls=0; references_reused=0
    for item in old['cases']:
        s=read(ROOT/item['path']);case=s['id'];energy=s['event']['task']['energy']
        assert energy==8.0
        cached=read(ROOT/'results/baseline_calibration_v1/cases'/(case+'.json'))['runs']['anchor_cost']
        val=verify(s,cached['plan']);assert val['valid'] and cached['optimal']
        assert abs(val['cost']-cached['validation']['cost']) < 2e-7
        u=val['cost'];references_reused+=1
        assert abs(settlement_value(s,cached['plan'],1,1)-u)<1e-10
        record=dict(case_id=case,input_path=item['path'],input_sha256=sha(ROOT/item['path']),
                    economic_reference=dict(cost=u,lower_bound=cached['lower_bound'],reused=True,validation=val),runs={})
        for alpha in ALPHAS:
            cap=u+alpha*.8*energy
            for kappa in SEGAN_GRID:
                label=f'segan_a{alpha}_k{kappa}';penalty=.8*kappa
                m=RepairModel(s);m.set_cost_cap(cap)
                r0=solve(m,weighted(m,penalty));calls+=1
                assert r0['plan'] is not None, 'No initial SEGAN incumbent; stop for review'
                v0=checked(s,r0['plan'],cap)
                j0=v0['cost']+penalty*v0['d_vpp']
                assert abs(j0-r0['objective'])<2e-5
                cost_scores=scores(s,r0['plan']);edge=sorted(cost_scores,key=lambda e:(-cost_scores[e],e))[0]
                m.bounds('participate|'+edge,0,0);m.set_total_cap(v0['d_vpp'])
                r1=solve(m,weighted(m,penalty));calls+=1
                selected,v,accepted=r0['plan'],v0,False
                if r1['plan'] is not None:
                    v1=checked(s,r1['plan'],cap,v0['d_vpp'],edge)
                    j1=v1['cost']+penalty*v1['d_vpp']
                    assert abs(j1-r1['objective'])<2e-5
                    if r0['optimal'] and r1['optimal']:
                        assert j1+2e-5>=j0, 'Restricted optimum below unrestricted optimum'
                    selected,v,accepted=r1['plan'],v1,True
                elif v0['max_control_changes'][edge] < 1e-9:
                    assert not r1['proven_infeasible'], 'Existing frozen witness contradicts infeasibility'
                r0['validation']=v0
                record['runs'][label]=dict(first_pass=r0,backup_pass=r1,cost_scores=cost_scores,
                       candidate=edge,backup_accepted=accepted,selected_plan=selected,selected_validation=v)
                rows.append(row_from(case,alpha,'segan',kappa,v,u,2,r0['solver_seconds']+r1['solver_seconds'],
                       first_optimal=r0['optimal'],second_status=r1['status'],backup_accepted=accepted,
                       initial_d=v0['d_vpp'],initial_n=v0['n_adjust'],initial_cost=v0['cost'],candidate=edge))
            for kappa in SETTLEMENT_GRID:
                bm,bp=1+.3*kappa,1-.2*kappa
                m=RepairModel(s);m.set_cost_cap(cap)
                r=solve(m,settlement_terms(m,bm,bp));calls+=1
                assert r['plan'] is not None, 'No settlement incumbent; stop for review'
                v=checked(s,r['plan'],cap)
                independent=settlement_value(s,r['plan'],bm,bp)
                assert abs(independent-r['objective'])<2e-5
                r.update(validation=v,independent_objective=independent,beta_minus=bm,beta_plus=bp)
                label=f'settlement_a{alpha}_k{kappa}';record['runs'][label]=r
                rows.append(row_from(case,alpha,'settlement',kappa,v,u,1,r['solver_seconds'],
                            first_optimal=r['optimal'],second_status='',backup_accepted='',
                            initial_d='',initial_n='',initial_cost='',candidate=''))
            write(OUT/'cases'/(case+'.json'),record)
            write(OUT/'progress.json',dict(completed_rows=len(rows),planned_rows=156,solver_calls=calls,case_id=case,alpha=alpha))
            print(f'{case} alpha={alpha}: {len(rows)}/156 configurations; {calls} calls',flush=True)
        with (OUT/'metrics.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    assert len(rows)==156 and calls==234
    ranking={}
    for method in ('segan','settlement'):
        groups=[]
        for parameter in (SEGAN_GRID if method=='segan' else SETTLEMENT_GRID):
            chosen=[r for r in rows if r['method']==method and r['parameter']==parameter]
            families=sorted({r['family'] for r in chosen})
            def balanced(field,scale):
                return sum(sum(r[field]/scale for r in chosen if r['family']==f)/sum(r['family']==f for r in chosen) for f in families)/len(families)
            groups.append(dict(parameter=parameter,configurations=len(chosen),
                 first_pass_optimal=sum(r['first_optimal'] for r in chosen),
                 d_over_event=balanced('d_vpp',8),n_over_non_event=balanced('n_strict',3),
                 extra_over_scale=balanced('extra_cost',6.4)))
        ranking[method]=sorted(groups,key=lambda g:(g['d_over_event'],g['n_over_non_event'],g['extra_over_scale'],g['parameter']))
    changed=[k for k,v in before.items() if sha(ROOT/k)!=v]
    write(OUT/'preservation.json',dict(checked=len(before),changed=changed,valid=not changed))
    assert not changed,'Original evidence modified'
    summary=dict(cases=13,configurations=156,solver_calls=calls,references_reused=references_reused,
                 all_output_plans_independently_valid=True,first_pass_optimal=sum(r['first_optimal'] for r in rows),
                 backup_acceptances=sum(r['backup_accepted'] is True for r in rows),
                 backup_proven_infeasible=sum(r['second_status']=='Infeasible' for r in rows),
                 rankings=ranking,formal_experiments_started=False,original_evidence_unchanged=True)
    write(OUT/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    main()
