"""Authorized objective-module ablations, frozen data/core, exact solve trace."""
import csv
import json
from pathlib import Path
import time

from run_pilot import require,write,sha
from run_tracking_comparison import sources,read,LABELS
import vpp_repair.ablations as ablation_api
from vpp_repair.model import RepairModel
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'results'/'formal_ablations_v1'
VARIANTS = ('without_plan','without_coordination','without_final_cost')
ORDERS = dict(without_plan=['coordination','economic'],
              without_coordination=['total','economic'],
              without_final_cost=['total','coordination'])

def save_rows(rows):
    with (OUT/'comparisons.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def main():
    require(not OUT.exists(),'refusing to overwrite ablation evidence')
    items = sources()
    protected = {str(p.relative_to(ROOT)):sha(p) for folder in ('datasets','vpp_repair','results')
                 for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    manifest = dict(cases=items,case_count=60,budget_labels=LABELS,variants=VARIANTS,
        configurations=540,planned_new_solver_calls=1080,shared_economic_references_reused=True,
        full_method_results_reused=True,full_method_paired_configurations=180,
        cost_cap_rule='shared economic reference actual cost + saved epsilon',
        objective_orders=ORDERS,solver='unchanged HiGHS RepairModel',
        phase_time_limit_seconds=20,physical_constraints_removed=False,
        trace_policy='in-process subclass records actual calls and backend state; core files unchanged',
        tolerances=dict(numerical_d_kwh=2e-5,numerical_cost_cny=2e-5,
                        practical_d='max(0.01 kWh,1% of event energy)',practical_control_kw=.01),
        cost_module_isolation='also compare saved full coordination plan with saved full final plan; '
            'exact same prefix, zero extra solves',
        reporting_policy='retain ties, adverse outcomes and conditional tradeoffs; '
            'no target-value-based dataset or algorithm changes',
        protected_sha256=protected,runner_sha256=sha(Path(__file__)))
    OUT.mkdir(parents=True)
    write(OUT/'manifest.json',manifest)
    rows,calls = [],0
    started = time.perf_counter()
    for item in items:
        state,own = read(ROOT/item['input_path']),read(ROOT/item['own_path'])
        economic = own['common_economic_reference']
        econ_check = verify(state,economic['plan'])
        require(econ_check['valid'] and economic['optimal'],'invalid common reference')
        expected_model = RepairModel(state)
        expected_digest = expected_model.digest()
        require(expected_model.audit_backend()['valid'],'expected physical backend audit failed')
        rec = dict(case_id=item['case_id'],input_path=item['input_path'],own_path=item['own_path'],
                   expected_initial_digest=expected_digest,runs={})
        for label in LABELS:
            full = own['configurations'][label]
            epsilon = full['summary']['epsilon']
            cap = econ_check['cost']+epsilon
            full_check = verify(state,full['detail']['selected_plan'],cost_cap=cap)
            require(full_check['valid'],'saved full method invalid')
            rec['runs'][label] = {}
            for variant in VARIANTS:
                run = dict(epsilon=epsilon,cost_cap=cap,trace=[])
                rec['runs'][label][variant] = run
                wall_started = time.perf_counter()
                class TracedModel(RepairModel):
                    def __init__(self,current):
                        construction = time.perf_counter()
                        super().__init__(current)
                        run['initial_digest'] = self.digest()
                        run['initial_backend_audit'] = self.audit_backend()
                        run['build_and_initial_audit_seconds'] = time.perf_counter()-construction
                        require(self.digest()==expected_digest,'ablation initial physics changed')
                        require(run['initial_backend_audit']['valid'],'ablation physical backend mismatch')

                    def solve(self,objective):
                        nonlocal calls
                        calls += 1
                        constraints = {}
                        for name in ('economic_cap','total_deviation_cap','scope_limit'):
                            row = self.rows.get(name)
                            constraints[name] = ({'lower':row['lower'],'upper':row['upper']} if row else None)
                        trace = dict(sequence=calls,objective=objective,model_digest=self.digest(),
                                     phase_constraints=constraints)
                        run['trace'].append(trace)
                        try:
                            result = super().solve(objective)
                            trace.update(status=result['status'],optimal=result['optimal'],
                                objective_value=result['objective'],lower_bound=result['lower_bound'],
                                solver_seconds=result['solver_seconds'])
                            return result
                        except Exception as exc:
                            trace['exception'] = str(exc)
                            write(OUT/'cases'/(item['case_id']+'.json'),rec)
                            raise
                original_class = ablation_api.RepairModel
                ablation_api.RepairModel = TracedModel
                try:
                    result = ablation_api.solve_ablation(state,variant,epsilon=epsilon,
                                                         shared_economic=economic)
                except Exception as exc:
                    run['exception'] = str(exc)
                    write(OUT/'cases'/(item['case_id']+'.json'),rec)
                    raise
                finally:
                    ablation_api.RepairModel = original_class
                run['result'] = result
                run['wall_seconds'] = time.perf_counter()-wall_started
                require([t['objective'] for t in run['trace']]==ORDERS[variant],
                        'unexpected objective sequence')
                selected = result.get('selected_plan')
                check = verify(state,selected,cost_cap=cap) if selected else None
                if selected:
                    require(check['valid'],'invalid ablation final plan')
                row = dict(case_id=item['case_id'],stage=item['stage'],family=own['family'],
                    profile=own.get('profile','ORIGINAL'),budget=label,variant=variant,
                    event_energy=state['event']['task']['energy'],epsilon=epsilon,common_cost_cap=cap,
                    status=result['status'],valid=bool(check and check['valid']),
                    all_new_phases_optimal=all(t['optimal'] for t in run['trace']),
                    full_cost=full_check['cost'],full_d=full_check['d_vpp'],full_n=full_check['n_adjust'],
                    ablated_cost=check['cost'] if check else None,ablated_d=check['d_vpp'] if check else None,
                    ablated_n=check['n_adjust'] if check else None,
                    d_difference=full_check['d_vpp']-check['d_vpp'] if check else None,
                    n_difference=full_check['n_adjust']-check['n_adjust'] if check else None,
                    cost_difference=full_check['cost']-check['cost'] if check else None,
                    new_solver_calls=len(run['trace']),solver_seconds=sum(t['solver_seconds'] for t in run['trace']),
                    wall_seconds=run['wall_seconds'],
                    full_saved_solver_seconds=full['summary']['config_solver_seconds_excluding_shared_reference'],
                    full_saved_wall_seconds=full['summary']['config_wall_excluding_shared_reference'],
                    shared_economic_seconds=own['common_economic_wall_seconds'])
                run['summary'] = row
                rows.append(row)
                write(OUT/'cases'/(item['case_id']+'.json'),rec)
                save_rows(rows)
                write(OUT/'progress.json',dict(completed=len(rows),planned=540,new_solver_calls=calls,
                    planned_solver_calls=1080,case_id=item['case_id'],budget=label,variant=variant,
                    elapsed_seconds=time.perf_counter()-started))
                print(f"{len(rows):03d}/540 {item['case_id']} {label} {variant}: "
                    f"D {row['full_d']:.5g}/{row['ablated_d']}; "
                    f"N {row['full_n']}/{row['ablated_n']}; Cdiff={row['cost_difference']}",flush=True)
    changed = [p for p,d in protected.items() if sha(ROOT/p)!=d]
    write(OUT/'preservation.json',dict(valid=not changed,changed=changed,checked_files=len(protected)))
    require(not changed,'existing evidence changed')
    completion = dict(configurations=len(rows),new_solver_calls=calls,
        optimal_new_phases=sum(t['optimal'] for p in (OUT/'cases').glob('*.json')
            for variants in read(p)['runs'].values() for r in variants.values() for t in r['trace']),
        valid=sum(r['valid'] for r in rows),
        all_new_phases_optimal_configurations=sum(r['all_new_phases_optimal'] for r in rows),
        elapsed_seconds=time.perf_counter()-started)
    write(OUT/'completion.json',completion)
    print(json.dumps(completion,ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
