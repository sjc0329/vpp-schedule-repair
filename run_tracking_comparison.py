"""First literature mechanism: frozen T9, 60 cases x three COMMON cost caps.

Budget-controlled adaptation, not replication of Naughton's entire architecture.
No proposed-method plan is passed to SCIP; only the shared economic reference.
"""
import csv
import json
import math
from pathlib import Path
import time
import traceback

from run_pilot import require,write,sha
from vpp_repair.baseline_objectives import evaluate_tracking,tracking_objective
from vpp_repair.scip_baselines import ScipBaseline
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'results'/'tracking_comparison_v1'
LABELS = ('alpha_0','alpha_0p1','alpha_0p3')

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def sources():
    answer = []
    for stage in ('main_stage1_v1','main_stage2_v1'):
        for path in sorted((ROOT/'datasets'/stage).glob('*-event-state.json')):
            name = path.name.removesuffix('-event-state.json')
            answer.append(dict(stage=stage,case_id=name,
                input_path=str(path.relative_to(ROOT)),
                own_path=str((ROOT/'results'/stage/'cases'/(name+'.json')).relative_to(ROOT))))
    require(len(answer)==60 and len({r['case_id'] for r in answer})==60,'expected 60 unique cases')
    return answer

def add_economic_seed(adapter,plan,cost_scale):
    """Reconstruct every canonical variable; SCIP checks the complete solution."""
    started = time.perf_counter()
    state = adapter.state
    physical = verify(state,plan,cost_cap=adapter.cost_cap)
    require(physical['valid'],'economic seed invalid under common budget')
    seed = adapter.m.createSol()
    values = {}
    for name,variable in adapter.variables.items():
        parts = name.split('|')
        kind,owner = parts[:2]
        if kind=='participate':
            value = 1.0  # Allowed participation; actual N is independently measured.
        else:
            k = int(parts[2])
            if kind in ('q','r'):
                value = plan['edges'][owner]['q' if kind=='q' else 'renewable'][k]
            elif kind=='abs':
                value = abs(plan['edges'][owner]['q'][k]-state['reference']['edges'][owner]['q'][k])
            elif kind=='abs_total':
                value = abs(sum(plan['edges'][e]['q'][k]-state['reference']['edges'][e]['q'][k]
                                for e in state['load']))
            elif kind in ('E','c','d'):
                value = plan['resources'][owner][dict(E='energy',c='charge',d='discharge')[kind]][k]
            elif kind=='mode':
                value = (plan['grid'] if owner=='grid' else plan['resources'][owner])['mode'][k]
            elif kind in ('buy','sell'):
                value = plan['grid'][kind][k]
            elif kind=='u':
                value = plan['tasks'][owner][k]
            else:
                raise ValueError('unmapped seed variable '+name)
        if adapter.source.variables[name]['binary']:
            value = float(round(value))
        values[name] = value
        adapter.m.setSolVal(seed,variable,value)
    metric = evaluate_tracking(state,plan)
    quadratic = 10*(.75*metric['power_squared']+.25*metric['energy_squared'])
    extras = []
    for variable in adapter.m.getVars(transformed=False):
        if variable.name not in adapter.variables:
            require(variable.name=='tracking_quadratic_epigraph','unexpected auxiliary variable')
            adapter.m.setSolVal(seed,variable,quadratic+1e-8)
            extras.append(variable.name)
    require(len(extras)==1,'missing quadratic epigraph')
    # Also check canonical bounds/rows independently of SCIP's solution checker.
    max_bound = max(max(data['lower']-values[name],values[name]-data['upper'],0)
                    for name,data in adapter.source.variables.items())
    max_row = 0.0
    for row in adapter.source.rows.values():
        activity = sum(c*values[name] for name,c in row['coeff'].items())
        max_row = max(max_row,row['lower']-activity,activity-row['upper'],0)
    feasible = bool(adapter.m.checkSol(seed,printreason=False,completely=True,original=True))
    require(feasible and max(max_bound,max_row)<2e-7,'SCIP rejected complete economic seed')
    stored = bool(adapter.m.addSol(seed,free=True))
    require(stored,'SCIP did not store feasible seed')
    return dict(valid=True,stored=stored,scip_complete_check=feasible,
        canonical_bound_residual=max_bound,canonical_row_residual=max_row,
        independent_validation=physical,
        objective=physical['cost']/cost_scale+quadratic,seconds=time.perf_counter()-started,
        source='shared economic reference only; no proposed repair or previous budget result')

def make_row(item,label,state,own,result,seed,cap,epsilon,wall):
    check = result.get('validation')
    record = own['configurations'][label]
    proposed = record['summary']
    own_check = verify(state,record['detail']['selected_plan'],cost_cap=cap)
    require(own_check['valid'],'saved proposed plan fails common-budget audit')
    row = dict(case_id=item['case_id'],stage=item['stage'],family=own['family'],
        profile=own.get('profile','ORIGINAL'),budget=label,alpha=proposed['alpha'],epsilon=epsilon,
        common_cost_cap=cap,event_energy=state['event']['task']['energy'],
        baseline_status=result.get('status','ERROR'),baseline_optimal=bool(result.get('optimal')),
        baseline_valid=bool(check and check['valid']),
        baseline_cost=check['cost'] if check else None,
        baseline_d=check['d_vpp'] if check else None,
        baseline_n=check['n_adjust'] if check else None,
        own_cost=own_check['cost'],own_d=own_check['d_vpp'],own_n=own_check['n_adjust'],
        own_total_optimal=proposed['total_optimal'],
        baseline_tracking_objective=(result.get('independent_objective') or {}).get('objective'),
        baseline_lower_bound=result.get('lower_bound'),baseline_absolute_gap=result.get('absolute_gap'),
        baseline_build_seconds=result.get('build_seconds'),seed_seconds=seed.get('seconds'),
        baseline_solver_seconds=result.get('solver_seconds'),baseline_wall_seconds=wall,
        own_saved_wall_seconds=proposed['config_wall_excluding_shared_reference'],
        own_saved_solver_seconds=proposed['config_solver_seconds_excluding_shared_reference'],
        shared_economic_seconds=own['common_economic_wall_seconds'],
        timing_caution='different solvers and historical run times; not an isolated algorithm speed comparison')
    if check:
        row.update(d_difference=own_check['d_vpp']-check['d_vpp'],
            n_difference=own_check['n_adjust']-check['n_adjust'],
            cost_difference=own_check['cost']-check['cost'])
    else:
        row.update(d_difference=None,n_difference=None,cost_difference=None)
    return row

def save_table(rows):
    with (OUT/'comparisons.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def main():
    require(not OUT.exists(),'refusing to overwrite comparison evidence')
    items = sources()
    frozen = read(ROOT/'results/baseline_calibration_v1/parameter_candidates.json')
    require(frozen['candidates']['tracking']['configuration']==dict(penalty=10.,rho=.75),'T9 changed')
    protected = {str(p.relative_to(ROOT)):sha(p) for folder in ('datasets','vpp_repair','results')
                 for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    manifest = dict(cases=items,planned_solver_calls=180,case_count=60,
        budget_labels=LABELS,penalty=10.,rho=.75,cost_scale_rule='.8*event_energy',
        cost_cap_rule='saved common economic upper bound + saved epsilon',solver_limit_seconds=20,
        seed_policy='verified shared economic reference, complete SCIP-checked seed, no proposed repair',
        calibration_protocol_change='formal runs have common hard budgets and an economic warm start; '
            'calibration ran uncapped/cold; parameters are frozen and not retuned',
        research_scope='budget-controlled adaptation of squared power/state tracking mechanism; '
            'not full original-paper replication; first literature baseline only',
        comparisons='paired C,D,N on common feasible sets; actual failed/timed solves retained',
        timing_scope='baseline fresh construction/import/seed separated from solve; proposed historical '
            'persistent-model times are descriptive only, no algorithm-only speed attribution',
        protected_sha256=protected,runner_sha256=sha(Path(__file__)),
        calibration_candidates_sha256=sha(ROOT/'results/baseline_calibration_v1/parameter_candidates.json'))
    OUT.mkdir(parents=True)
    write(OUT/'manifest.json',manifest)
    rows,errors,calls = [],[],0
    started = time.perf_counter()
    for item in items:
        state = read(ROOT/item['input_path'])
        own = read(ROOT/item['own_path'])
        economic = own['common_economic_reference']
        econ_check = verify(state,economic['plan'])
        require(econ_check['valid'] and economic['optimal'],'unproved common economic reference')
        scale = .8*state['event']['task']['energy']
        rec = dict(case_id=item['case_id'],input_path=item['input_path'],own_path=item['own_path'],runs={})
        for label in LABELS:
            epsilon = own['configurations'][label]['summary']['epsilon']
            cap = econ_check['cost']+epsilon
            wall_started = time.perf_counter()
            seed,result = {},{}
            try:
                model = ScipBaseline(state,cost_cap=cap,time_limit=20)
                model.set_tracking_objective(cost_scale=scale,penalty=10.,rho=.75)
                seed = add_economic_seed(model,economic['plan'],scale)
                calls += 1
                result = model.solve()
                require(result.get('validation') is not None,'accepted seed was lost by solver')
            except Exception as exc:
                errors.append(dict(case_id=item['case_id'],budget=label,error=str(exc)))
                result.update(exception=str(exc),traceback=traceback.format_exc())
            wall = time.perf_counter()-wall_started
            row = make_row(item,label,state,own,result,seed,cap,epsilon,wall)
            rows.append(row)
            rec['runs'][label] = dict(cost_scale=scale,cost_cap=cap,epsilon=epsilon,
                seed_audit=seed,result=result,summary=row)
            write(OUT/'cases'/(item['case_id']+'.json'),rec)
            save_table(rows)
            write(OUT/'progress.json',dict(completed=len(rows),planned=180,solver_calls=calls,
                case_id=item['case_id'],budget=label,elapsed_seconds=time.perf_counter()-started,errors=errors))
            print(f"{len(rows):03d}/180 {item['case_id']} {label}: {row['baseline_status']}; "
                  f"D own={row['own_d']:.6g}, tracking={row['baseline_d']}; "
                  f"N own={row['own_n']}, tracking={row['baseline_n']}",flush=True)
            # A failed model/seed setup is an implementation issue, not a scientific outcome.
            require('exception' not in result,'setup or solve exception: inspect saved record before continuing')
    changed = [p for p,d in protected.items() if sha(ROOT/p)!=d]
    write(OUT/'preservation.json',dict(valid=not changed,changed=changed,checked_files=len(protected)))
    require(not changed,'existing evidence changed')
    write(OUT/'completion.json',dict(completed=len(rows),solver_calls=calls,errors=errors,
        optimal=sum(r['baseline_optimal'] for r in rows),valid=sum(r['baseline_valid'] for r in rows),
        elapsed_seconds=time.perf_counter()-started))
    print(json.dumps(read(OUT/'completion.json'),ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
