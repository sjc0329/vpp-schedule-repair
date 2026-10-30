"""Frozen S5 quality/stability adaptation, common budgets, all payoff costs counted."""
import csv
import json
from pathlib import Path
import time
import traceback

from run_pilot import require,write,sha
from run_tracking_comparison import sources,read,LABELS
from run_baseline_calibration import normalization
from vpp_repair.scip_baselines import ScipBaseline
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'results'/'scalar_comparison_v1'
WEIGHTS = dict(cost=.45,deviation=.45,participation=.10)

def economic_seed(adapter,plan):
    started = time.perf_counter()
    state,values = adapter.state,{}
    validation = verify(state,plan,cost_cap=adapter.cost_cap)
    require(validation['valid'],'invalid economic seed')
    solution = adapter.m.createSol()
    for name,variable in adapter.variables.items():
        parts = name.split('|')
        kind,owner = parts[:2]
        if kind=='participate':
            value = 1.
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
                raise ValueError('unmapped variable '+name)
        if adapter.source.variables[name]['binary']:
            value = float(round(value))
        values[name] = value
        adapter.m.setSolVal(solution,variable,value)
    require(len(adapter.m.getVars(transformed=False))==len(values),'unexpected extra variable')
    max_bound = max(max(d['lower']-values[k],values[k]-d['upper'],0)
                    for k,d in adapter.source.variables.items())
    max_row = 0.
    for row in adapter.source.rows.values():
        activity = sum(c*values[k] for k,c in row['coeff'].items())
        max_row = max(max_row,row['lower']-activity,activity-row['upper'],0)
    feasible = bool(adapter.m.checkSol(solution,printreason=False,completely=True,original=True))
    require(feasible and max(max_bound,max_row)<2e-7,'economic seed failed complete check')
    stored = bool(adapter.m.addSol(solution,free=True))
    require(stored,'economic seed not stored')
    return dict(valid=True,stored=stored,scip_complete_check=feasible,
        canonical_bound_residual=max_bound,canonical_row_residual=max_row,
        independent_validation=validation,seconds=time.perf_counter()-started,
        source='shared economic reference, never own-method repair')

def save_table(rows):
    with (OUT/'comparisons.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def main():
    require(not OUT.exists(),'refusing to overwrite formal comparison')
    items = sources()
    frozen = read(ROOT/'results/baseline_calibration_v1/parameter_candidates.json')
    require(frozen['candidates']['scalar']['configuration']==WEIGHTS,'S5 changed')
    protected = {str(p.relative_to(ROOT)):sha(p) for folder in ('datasets','vpp_repair','results')
                 for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    manifest = dict(cases=items,case_count=60,paired_configurations=180,planned_solver_calls=720,
        payoff_anchor_calls=540,scalar_objective_calls=180,budget_labels=LABELS,weights=WEIGHTS,
        cost_scale_rule='.8*event_energy',cost_cap_rule='shared economic upper bound + saved epsilon',
        solver_limit_seconds=20,seed_policy='same checked economic reference for every objective',
        normalization_policy='three optimal anchors per common budget; observed anchor spans; '
            'positive fallback for zero span; no clipping; all anchor costs counted',
        calibration_protocol_change='hard common budget and economic warm start; no parameter retuning',
        source_scope='Koksalmis et al. 2025 quality/stability normalization mapped to VPP C,D,N; '
            'mechanism adaptation, not the original runway system or heuristic algorithms',
        timing_scope='all four builds/seeds/solves recorded; own times historical and solver differs; '
            'do not attribute speed differences to algorithm alone',
        outcome_policy='retain all outcomes; do not strengthen or weaken the frozen baseline after seeing results',
        protected_sha256=protected,runner_sha256=sha(Path(__file__)),
        calibration_sha256=sha(ROOT/'results/baseline_calibration_v1/parameter_candidates.json'))
    OUT.mkdir(parents=True)
    write(OUT/'manifest.json',manifest)
    started = time.perf_counter()
    calls,rows = 0,[]
    for item in items:
        state,own = read(ROOT/item['input_path']),read(ROOT/item['own_path'])
        econ_plan = own['common_economic_reference']['plan']
        econ = verify(state,econ_plan)
        require(econ['valid'] and own['common_economic_reference']['optimal'],'invalid economic reference')
        rec = dict(case_id=item['case_id'],input_path=item['input_path'],own_path=item['own_path'],runs={})
        for label in LABELS:
            proposed = own['configurations'][label]
            epsilon = proposed['summary']['epsilon']
            cap,scale = econ['cost']+epsilon,.8*state['event']['task']['energy']
            config_started = time.perf_counter()
            config = dict(cost_cap=cap,cost_scale=scale,epsilon=epsilon,anchors={},seed_audits={})
            rec['runs'][label] = config
            def solve(role,configure):
                nonlocal calls
                model = ScipBaseline(state,cost_cap=cap,time_limit=20)
                configure(model)
                seed = economic_seed(model,econ_plan)
                config['seed_audits'][role] = seed
                calls += 1
                result = model.solve()
                require(result.get('validation') is not None,'checked seed lost')
                if role=='scalar':
                    config['result'] = result
                else:
                    config['anchors'][role] = result
                write(OUT/'cases'/(item['case_id']+'.json'),rec)
                write(OUT/'progress.json',dict(completed_pairs=len(rows),solver_calls=calls,
                    planned_solver_calls=720,case_id=item['case_id'],budget=label,role=role,
                    elapsed_seconds=time.perf_counter()-started))
                return result
            for metric in ('cost','deviation','participation'):
                solve(metric,lambda m,metric=metric:m.set_linear_objective(metric))
            result = None
            try:
                config['normalization'] = normalization(config['anchors'],state,scale)
            except ValueError as exc:
                # Unproved anchor is a normalization failure, never replaced by a made-up ideal.
                config['normalization_error'] = str(exc)
            if 'normalization' in config:
                result = solve('scalar',lambda m:m.set_scalar_objective(config['normalization'],WEIGHTS))
            wall = time.perf_counter()-config_started
            check = result['validation'] if result else None
            own_check = verify(state,proposed['detail']['selected_plan'],cost_cap=cap)
            require(own_check['valid'],'own plan invalid under common budget')
            all_results = list(config['anchors'].values())+([result] if result else [])
            anchor_results = list(config['anchors'].values())
            row = dict(case_id=item['case_id'],stage=item['stage'],family=own['family'],
                profile=own.get('profile','ORIGINAL'),budget=label,alpha=proposed['summary']['alpha'],
                epsilon=epsilon,common_cost_cap=cap,event_energy=state['event']['task']['energy'],
                baseline_status=result['status'] if result else 'UNPROVED_NORMALIZATION',
                baseline_optimal=bool(result and result['optimal']),baseline_valid=bool(check and check['valid']),
                baseline_cost=check['cost'] if check else None,baseline_d=check['d_vpp'] if check else None,
                baseline_n=check['n_adjust'] if check else None,own_cost=own_check['cost'],
                own_d=own_check['d_vpp'],own_n=own_check['n_adjust'],
                own_total_optimal=proposed['summary']['total_optimal'],
                d_difference=own_check['d_vpp']-check['d_vpp'] if check else None,
                n_difference=own_check['n_adjust']-check['n_adjust'] if check else None,
                cost_difference=own_check['cost']-check['cost'] if check else None,
                scalar_objective=(result['independent_objective']['objective'] if result else None),
                scalar_lower_bound=result['lower_bound'] if result else None,
                scalar_absolute_gap=result['absolute_gap'] if result else None,
                anchor_solver_calls=3,scalar_solver_calls=int(result is not None),
                anchor_solver_seconds=sum(r['solver_seconds'] for r in anchor_results),
                scalar_solver_seconds=result['solver_seconds'] if result else None,
                baseline_solver_seconds=sum(r['solver_seconds'] for r in all_results),
                baseline_build_seconds=sum(r['build_seconds'] for r in all_results),
                seed_seconds=sum(s['seconds'] for s in config['seed_audits'].values()),
                baseline_wall_seconds=wall,
                own_saved_wall_seconds=proposed['summary']['config_wall_excluding_shared_reference'],
                own_saved_solver_seconds=proposed['summary']['config_solver_seconds_excluding_shared_reference'],
                shared_economic_seconds=own['common_economic_wall_seconds'])
            config['summary'] = row
            rows.append(row)
            write(OUT/'cases'/(item['case_id']+'.json'),rec)
            save_table(rows)
            print(f"{len(rows):03d}/180, calls={calls}/720 {item['case_id']} {label}: "
                f"{row['baseline_status']}; D own={row['own_d']:.6g}, S5={row['baseline_d']}; "
                f"N own={row['own_n']}, S5={row['baseline_n']}",flush=True)
    changed = [p for p,d in protected.items() if sha(ROOT/p)!=d]
    write(OUT/'preservation.json',dict(valid=not changed,changed=changed,checked_files=len(protected)))
    require(not changed,'existing evidence changed')
    summary = dict(configurations=len(rows),solver_calls=calls,planned_solver_calls=720,
        optimal=sum(r['baseline_optimal'] for r in rows),valid=sum(r['baseline_valid'] for r in rows),
        normalization_failures=sum(r['baseline_status']=='UNPROVED_NORMALIZATION' for r in rows),
        elapsed_seconds=time.perf_counter()-started)
    write(OUT/'completion.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
