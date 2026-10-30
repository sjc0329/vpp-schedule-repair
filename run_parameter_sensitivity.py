"""Frozen budget and standby-price sensitivity; original evidence untouched."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import time

from run_pilot import require,write,sha
from vpp_repair.data import apply_event
from vpp_repair.model import RepairModel
from vpp_repair.pilot_data import empty_reference,event_snapshot
from vpp_repair.repair import select_repair
from vpp_repair.verify import verify

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'datasets/parameter_sensitivity_v1'
OUT=ROOT/'results/parameter_sensitivity_v1'
ALPHAS=(0.,.05,.1,.15,.2,.25,.3,.35,.4,.5,.6)
PRICES=(1.01,1.10,1.20)
CACHE={0.:'alpha_0',.1:'alpha_0p1',.3:'alpha_0p3'}

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def freeze():
    require(not DATA.exists() and not OUT.exists(),'refusing to overwrite sensitivity evidence')
    protected={str(p.relative_to(ROOT)):sha(p) for folder in ('datasets','vpp_repair','results',
        'chapter4_writing_v1','pre_draft_closeout_v1') for p in (ROOT/folder).rglob('*')
        if p.is_file() and '__pycache__' not in p.parts}
    DATA.mkdir(parents=True); OUT.mkdir(parents=True)
    selected=[('main_stage1_v1',f'TPL-G{family}-{level}-E08')
              for family in range(1,5) for level in range(1,4)]
    selected += [('main_stage2_v1',f'TPL-G{family}-{level}-DAY_SURPLUS')
                 for family in (3,4) for level in range(1,4)]
    selected += [('main_stage2_v1','TPL-G2-1-CLOUD_EVENING')]
    items=[]
    for stage,name in selected:
        state=read(ROOT/'datasets'/stage/(name+'-event-state.json'))
        own_path=ROOT/'results'/stage/'cases'/(name+'.json')
        own=read(own_path)
        items.append(dict(case_id=name,kind='original',family=own['family'],
            profile=own.get('profile','ORIGINAL'),price=1.01 if own['family']=='G4' else None,
            input_path=str((ROOT/'datasets'/stage/(name+'-event-state.json')).relative_to(ROOT)),
            source_result_path=str(own_path.relative_to(ROOT)),source_stage=stage,source_case_id=name))
    for level in range(1,4):
        for profile in ('ORIGINAL','DAY_SURPLUS'):
            stage='main_stage1_v1' if profile=='ORIGINAL' else 'main_stage2_v1'
            source_name=f'TPL-G4-{level}-E08' if profile=='ORIGINAL' else f'TPL-G4-{level}-DAY_SURPLUS'
            full_path=(ROOT/'datasets/formal_templates_v1'/f'TPL-G4-{level}-input.json'
                       if profile=='ORIGINAL' else ROOT/'datasets'/stage/(source_name+'-input.json'))
            pub_path=(ROOT/'datasets/formal_templates_v1'/f'TPL-G4-{level}-published.json'
                      if profile=='ORIGINAL' else ROOT/'datasets'/stage/(source_name+'-published.json'))
            original=read(full_path); publication=read(pub_path)
            throughput=sum((publication['resources'][r['id']]['charge'][k]+
                publication['resources'][r['id']]['discharge'][k])*original['dt']
                for r in original['resources'] for k in range(original['horizon']))
            require(throughput<2e-7,'fixed publication must have idle standby')
            for price in PRICES[1:]:
                name=f'SENS-G4-{level}-{profile}-C{round(price*100):03d}'
                full=deepcopy(original); full.update(id=name,qualification_only=False,
                    experimental_stage='price_sensitivity',profile=profile)
                full['event']['id']=name+'-event';full['event']['task']['energy']=8.
                for resource in full['resources']:
                    resource['wear']=price
                full['reference']=empty_reference(full)
                pub=deepcopy(publication);pub['case_id']=name
                pub_check=verify(full,pub)
                require(pub_check['valid'],'fixed publication invalid after price increase')
                snapshot=event_snapshot(full,pub)
                state,_=apply_event(snapshot,full['event'])
                paths={suffix:DATA/(name+'-'+suffix+'.json') for suffix in ('input','published','pre-event','event-state')}
                for suffix,obj in [('input',full),('published',pub),('pre-event',snapshot),('event-state',state)]:
                    write(paths[suffix],obj)
                items.append(dict(case_id=name,kind='price_variant',family='G4',profile=profile,
                    price=price,level=level,input_path=str(paths['event-state'].relative_to(ROOT)),
                    full_input_path=str(paths['input'].relative_to(ROOT)),
                    publication_path=str(paths['published'].relative_to(ROOT)),
                    snapshot_path=str(paths['pre-event'].relative_to(ROOT)),source_stage=stage,
                    source_case_id=source_name,source_full_input=str(full_path.relative_to(ROOT)),
                    source_publication=str(pub_path.relative_to(ROOT)),
                    source_publication_standby_throughput=throughput))
    require(len(items)==31 and len(selected)==19,'unexpected frozen selection')
    manifest=dict(case_count=31,original_selected_cases=19,new_price_cases=12,
        cases=items,alphas=ALPHAS,response_prices=PRICES,reference_price=.8,event_energy=8.,
        total_configurations=341,reused_configurations=57,new_repair_configurations=284,
        planned_new_solver_calls=876,planned_nominal_checks=12,planned_event_economic_solves=12,
        planned_repair_solves=852,baseline_parameters_unchanged=True,delta_d=0,
        selection_rule='All 12 original templates at E08; all 3 G3 and 3 G4 DAY_SURPLUS; '
            'local G2 CLOUD boundary. Price axis: all 3 G4 distributions x ORIGINAL/DAY x 3 prices.',
        planning_rule='Prices known before publication. Fixed idle-standby publication remains optimal '
            'under nonnegative increases in its unused throughput coefficients; verify with a fresh '
            'nominal economic solve but retain the original physical publication to isolate price effects.',
        prediction_scope='No universal best alpha. Report fixed-case C/D/N frontiers, observed grid '
            'plateaus and regime-specific full-preservation thresholds; larger grids are not population samples.',
        input_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(DATA.glob('*.json'))},
        protected_sha256=protected,runner_sha256=sha(Path(__file__)))
    write(DATA/'manifest.json',manifest)
    write(OUT/'manifest.json',manifest)
    return manifest

def save_rows(rows):
    with (OUT/'configurations.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def main():
    manifest=freeze();rows=[];calls=0;started=time.perf_counter()
    for item in manifest['cases']:
        state=read(ROOT/item['input_path']);name=item['case_id']
        rec=dict(case_id=name,item=item,configurations={},solve_trace=[])
        def save():
            write(OUT/'cases'/(name+'.json'),rec)
            write(OUT/'progress.json',dict(completed=len(rows),planned=341,new_solver_calls=calls,
                planned_new_solver_calls=876,case_id=name,elapsed_seconds=time.perf_counter()-started))
        class TracedModel(RepairModel):
            def __init__(self,s,role):
                self.role=role;super().__init__(s)
            def solve(self,objective):
                nonlocal calls
                calls+=1;trace=dict(sequence=calls,role=self.role,objective=objective)
                rec['solve_trace'].append(trace)
                result=super().solve(objective)
                trace.update(status=result['status'],optimal=result['optimal'],objective_value=result['objective'],
                    lower_bound=result['lower_bound'],solver_seconds=result['solver_seconds'])
                return result
        if item['kind']=='original':
            old=read(ROOT/item['source_result_path']);economic=old['common_economic_reference']
            rec['common_economic_reference_reused']=True
        else:
            full=read(ROOT/item['full_input_path']);pub=read(ROOT/item['publication_path'])
            nominal=TracedModel(full,'nominal_optimality_check').solve('economic')
            require(nominal['plan'] is not None,'nominal check has no plan')
            nominal['validation']=verify(full,nominal['plan'])
            fixed=verify(full,pub)
            require(nominal['optimal'] and nominal['validation']['valid'] and fixed['valid'],
                    'nominal optimality not established')
            require(abs(nominal['validation']['cost']-fixed['cost'])<2e-6,
                    'fixed publication no longer economic-optimal')
            rec['nominal_check']=dict(result=nominal,fixed_publication_validation=fixed,
                                     physical_publication_replaced=False)
            snapshot=read(ROOT/item['snapshot_path'])
            updated=TracedModel(snapshot,'event_economic');backend=id(updated.h)
            rec['incremental_edits']=updated.sync(state)
            require(id(updated.h)==backend and updated.digest()==RepairModel(state).digest(),
                    'incremental model mismatch')
            rec['backend_audit']=updated.audit_backend()
            require(rec['backend_audit']['valid'],'backend mismatch')
            economic=updated.solve('economic')
            rec['common_economic_reference_reused']=False
        require(economic['plan'] is not None and economic['optimal'],'economic reference not optimal')
        economic['validation']=verify(state,economic['plan'])
        require(economic['validation']['valid'],'invalid economic reference')
        rec['common_economic_reference']=economic
        upper=economic['validation']['cost']
        for alpha in ALPHAS:
            epsilon=alpha*.8*state['event']['task']['energy'];wall_start=time.perf_counter()
            reused=item['kind']=='original' and alpha in CACHE
            if reused:
                detail=old['configurations'][CACHE[alpha]]['detail']
            else:
                detail=select_repair(TracedModel(state,f'alpha={alpha}'),epsilon=epsilon,
                                     delta_d=0,shared_economic=economic)
            selected=detail.get('selected_plan');check=verify(state,selected,cost_cap=upper+epsilon) if selected else None
            require(check is not None and check['valid'],'sensitivity repair lacks valid plan')
            all_opt=all(p['optimal'] for p in detail['phases'].values())
            row=dict(case_id=name,source_case_id=item['source_case_id'],kind=item['kind'],family=item['family'],
                profile=item['profile'],response_price=item['price'],alpha=alpha,epsilon=epsilon,
                event_energy=state['event']['task']['energy'],cost=check['cost'],extra_cost=check['cost']-upper,
                d_vpp=check['d_vpp'],d_edge=check['d_edge'],n_adjust=check['n_adjust'],
                participants=';'.join(check['participants']),valid=check['valid'],all_phases_optimal=all_opt,
                status=detail['status'],reused=reused,new_solver_calls=0 if reused else 3,
                additional_wall_seconds=time.perf_counter()-wall_start)
            rec['configurations'][str(alpha)]=dict(summary=row,detail=detail)
            rows.append(row);save();save_rows(rows)
        print(f"{len(rows):03d}/341 calls={calls}/876 {name}: D0={rec['configurations']['0.0']['summary']['d_vpp']:.5g}, "
              f"D06={rec['configurations']['0.6']['summary']['d_vpp']:.5g}",flush=True)
    changed=[p for p,d in manifest['protected_sha256'].items() if sha(ROOT/p)!=d]
    write(OUT/'preservation.json',dict(valid=not changed,changed=changed,checked_files=len(manifest['protected_sha256'])))
    require(not changed,'existing evidence changed')
    require(calls==876 and len(rows)==341,'actual call count differs from frozen plan')
    completion=dict(configurations=len(rows),new_solver_calls=calls,reused=sum(r['reused'] for r in rows),
        valid=sum(r['valid'] for r in rows),all_phases_optimal=sum(r['all_phases_optimal'] for r in rows),
        elapsed_seconds=time.perf_counter()-started)
    write(OUT/'completion.json',completion);print(json.dumps(completion),flush=True)

if __name__=='__main__':
    main()
