"""Authorized 13-case calibration; evaluation inputs used for overlap exclusion only."""
import hashlib
import json
import math
from pathlib import Path
import time
import traceback

from run_pilot import require, write, sha
from vpp_repair.scip_baselines import ScipBaseline

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results' / 'baseline_calibration_v1'
PHYSICAL_KEYS = ('horizon','prefix','dt','interface_limit','load','pv','wind',
                 'import_limit','export_limit','buy_price','sell_price','resources','tasks')
TRACKING = [(f'T{j+1}', dict(penalty=p,rho=r))
            for j,(p,r) in enumerate((p,r) for p in (.1,1.,10.) for r in (.25,.5,.75))]
WEIGHTS = [(1/3,1/3,1/3),(.6,.2,.2),(.2,.6,.2),(.2,.2,.6),
           (.45,.45,.1),(.45,.1,.45),(.1,.45,.45)]
SCALAR = [(f'S{j+1}',dict(zip(('cost','deviation','participation'),w)))
          for j,w in enumerate(WEIGHTS)]

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def fingerprint(state):
    payload = {k:state[k] for k in PHYSICAL_KEYS}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

def protected_files():
    return sorted(p for folder in ('datasets','vpp_repair') for p in (ROOT/folder).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts) + [
        p for p in sorted((ROOT/'results').rglob('*')) if p.is_file() and OUT not in p.parents]

def select_cases():
    evaluation = {}
    for folder in ('main_stage1_v1','main_stage2_v1'):
        for path in sorted((ROOT/'datasets'/folder).glob('*-event-state.json')):
            evaluation.setdefault(fingerprint(read(path)),[]).append(str(path.relative_to(ROOT)))
    selected,excluded,seen = [],[],{}
    for folder in ('pilot_v1_corrected','absorption_v1'):
        for path in sorted((ROOT/'datasets'/folder).glob('*-event-state.json')):
            state = read(path)
            digest = fingerprint(state)
            if digest in evaluation or digest in seen:
                excluded.append(dict(path=str(path.relative_to(ROOT)),
                    reason='evaluation_overlap' if digest in evaluation else 'duplicate_calibration',
                    matches=evaluation.get(digest,[seen.get(digest)])))
                continue
            seen[digest] = str(path.relative_to(ROOT))
            selected.append(dict(path=str(path.relative_to(ROOT)),case_id=state['id'],
                                 physical_sha256=digest,input_sha256=sha(path)))
    require(len(selected)==13 and len(excluded)==2,'unexpected split')
    return selected,excluded

def accepted(result):
    return bool(result.get('optimal') and (result.get('validation') or {}).get('valid')
                and result['import_audit']['valid']
                and result['import_audit']['source_backend_audit']['valid']
                and result.get('objective_recalculation_error',math.inf)<2e-5)

def normalization(anchors,state,scale):
    require(all(accepted(r) for r in anchors.values()),'unproved or invalid payoff anchor')
    names = dict(cost='cost',deviation='d_vpp',participation='n_adjust')
    values = {k:{m:r['validation'][n] for m,n in names.items()} for k,r in anchors.items()}
    fallback = dict(cost=scale,deviation=max(1,state['event']['task']['energy']),
                    participation=max(1,len(state['load'])-1))
    answer = {}
    for metric in names:
        ideal = values[metric][metric]
        high = max(v[metric] for v in values.values())
        span = max(0,high-ideal)
        answer[metric] = dict(ideal=ideal,observed_anchor_max=high,
            scale=span if span>1e-8 else fallback[metric],fallback_used=span<=1e-8,
            observed_max_is_global_bound=False)
    return answer

def summarize(manifest):
    records = [read(p) for p in sorted((OUT/'cases').glob('*.json'))]
    rankings = {}
    # Frozen task-based rule: coverage within epsilon=.3*.8*E first;
    # then family-balanced D/E, N/non-event Edges, extra cost/(.8*E).
    # No formal-set results or proposed-method outputs enter this ranking.
    for kind,grid in (('tracking',TRACKING),('scalar',SCALAR)):
        table = []
        for label,config in grid:
            families,complete,proved,within,valid = {},True,0,0,0
            for rec in records:
                result = rec['runs'].get(label,{})
                check = result.get('validation')
                economic = rec['runs'].get('anchor_cost',{}).get('validation')
                if not check or not check.get('valid') or not economic:
                    complete = False
                    continue
                valid += 1
                proved += int(accepted(result))
                extra = check['cost']-economic['cost']
                within += int(extra <= .3*rec['cost_scale']+2e-6)
                family = rec['family']
                families.setdefault(family,[]).append((check['d_vpp']/rec['event_energy'],
                    check['n_adjust']/rec['non_event_edges'],max(0,extra)/rec['cost_scale']))
            means = [sum(sum(v[j] for v in group)/len(group) for group in families.values())/
                     len(families) for j in range(3)] if families else [None]*3
            table.append(dict(parameter_id=label,configuration=config,valid=valid,
                optimal=proved,within_reference_budget=within,complete=complete,
                family_balanced_d=means[0],family_balanced_n=means[1],family_balanced_extra=means[2]))
        table.sort(key=lambda x:(not x['complete'],-x['within_reference_budget'],
            x['family_balanced_d'] if x['family_balanced_d'] is not None else math.inf,
            x['family_balanced_n'] if x['family_balanced_n'] is not None else math.inf,
            x['family_balanced_extra'] if x['family_balanced_extra'] is not None else math.inf,
            x['parameter_id']))
        rankings[kind] = table
    results = [r for rec in records for r in rec['runs'].values()]
    summary = dict(cases=len(records),solver_calls=len(results),planned_solver_calls=247,
        valid=sum(bool((r.get('validation') or {}).get('valid')) for r in results),
        optimal=sum(bool(r.get('optimal')) for r in results),
        accepted=sum(accepted(r) for r in results),errors=sum('exception' in r for r in results),
        rankings=rankings,recommendations={k:v[0]['parameter_id'] for k,v in rankings.items()},
        recommendation_status='eligible_for_freeze' if all(
            v[0]['complete'] and v[0]['optimal']==13 for v in rankings.values()) else 'requires_review',
        caution='Calibration cases are related designed fixtures, not an independent population sample. '
                'Budget compliance here is an assessment, not a hard constraint in baseline solves. '
                'No formal comparison or ablation has been executed.')
    write(OUT/'summary.json',summary)
    return summary

def main():
    require(not OUT.exists(),'refusing to overwrite calibration evidence')
    selected,excluded = select_cases()
    before = {str(p.relative_to(ROOT)):sha(p) for p in protected_files()}
    manifest = dict(cases=selected,excluded=excluded,planned_solver_calls=247,
        tracking_grid=TRACKING,scalar_grid=SCALAR,anchors_per_case=3,
        solver_limit_seconds=20,cost_scale_rule='.8*event_energy',
        no_baseline_cost_cap=True,assessment_budget_rule='.3*.8*event_energy',
        selection_rule='Lex: complete coverage; count within assessment budget; '
            'family-balanced mean D/event_energy; mean N/non-event Edges; mean nonnegative extra cost/scale; parameter id.',
        family_rule='ECON, SCOPE, TIME, ABS-LOCAL, ABS-REMOTE have equal mean weight',
        formal_results_used_for_selection=False,
        protected_sha256=before,runner_sha256=sha(Path(__file__)))
    OUT.mkdir(parents=True)
    write(OUT/'manifest.json',manifest)
    started = time.perf_counter()
    calls = 0
    for item in selected:
        state = read(ROOT/item['path'])
        name = state['id']
        energy = state['event']['task']['energy']
        scale = .8*energy
        rec = dict(case_id=name,input_path=item['path'],event_energy=energy,cost_scale=scale,
            non_event_edges=max(1,len(state['load'])-1),family=name.rsplit('-',1)[0],runs={})
        def run(label,configure):
            nonlocal calls
            calls += 1
            try:
                model = ScipBaseline(state,time_limit=20)
                configure(model)
                result = model.solve()
            except Exception as exc:
                result = dict(exception=str(exc),traceback=traceback.format_exc())
            rec['runs'][label] = result
            write(OUT/'cases'/(name+'.json'),rec)
            write(OUT/'progress.json',dict(solver_calls=calls,planned=247,case_id=name,
                parameter_id=label,status=result.get('status','ERROR'),elapsed_seconds=time.perf_counter()-started))
            print(f"{calls:03d}/247 {name} {label}: {result.get('status',result.get('exception'))}",flush=True)
            return result
        anchors = {}
        for metric in ('cost','deviation','participation'):
            anchors[metric] = run('anchor_'+metric,lambda m,metric=metric:m.set_linear_objective(metric))
        for label,config in TRACKING:
            run(label,lambda m,config=config:m.set_tracking_objective(cost_scale=scale,**config))
        try:
            norm = normalization(anchors,state,scale)
            rec['normalization'] = norm
        except Exception as exc:
            rec['normalization_error'] = str(exc)
            write(OUT/'cases'/(name+'.json'),rec)
            continue
        for label,weights in SCALAR:
            run(label,lambda m,weights=weights:m.set_scalar_objective(norm,weights))
    summary = summarize(manifest)
    changed = [p for p,d in before.items() if sha(ROOT/p)!=d]
    write(OUT/'preservation.json',dict(valid=not changed,changed=changed,checked_files=len(before)))
    require(not changed,'protected evidence changed')
    print(json.dumps({k:summary[k] for k in ('solver_calls','valid','optimal','errors',
        'recommendations','recommendation_status')},ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
