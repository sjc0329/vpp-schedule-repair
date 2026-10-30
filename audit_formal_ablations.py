"""Physical/phase-removal audit and conditional module attribution, no solves."""
import json
from statistics import mean

from run_pilot import require,write,sha
from run_formal_ablations import ROOT,OUT,VARIANTS,ORDERS,LABELS,read
from audit_tracking_comparison import control_changes
from vpp_repair.model import RepairModel
from vpp_repair.verify import verify

def summarize(rows):
    valid = [r for r in rows if r['valid']]
    def counts(field,tolerance=2e-5):
        return dict(full_lower=sum(r[field]<-tolerance for r in valid),
                    tied=sum(abs(r[field])<=tolerance for r in valid),
                    full_higher=sum(r[field]>tolerance for r in valid))
    practical_d = dict(full_lower=0,tied=0,full_higher=0)
    for row in valid:
        difference,tolerance = row['d_difference'],max(.01,.01*row['event_energy'])
        practical_d['full_lower' if difference < -tolerance else 'full_higher' if difference > tolerance else 'tied'] += 1
    return dict(configurations=len(rows),valid=len(valid),
        all_phases_optimal=sum(r['all_new_phases_optimal'] for r in rows),
        numerical_d=counts('d_difference'),practical_d=practical_d,
        n=counts('n_difference',0),cost=counts('cost_difference'),
        means=dict(full_d=mean(r['full_d'] for r in valid),ablated_d=mean(r['ablated_d'] for r in valid),
                   full_n=mean(r['full_n'] for r in valid),ablated_n=mean(r['ablated_n'] for r in valid),
                   full_cost=mean(r['full_cost'] for r in valid),ablated_cost=mean(r['ablated_cost'] for r in valid),
                   full_minus_ablated_cost=mean(r['cost_difference'] for r in valid)) if valid else None,
        retained_d_difference_max=max((abs(r['d_difference']) for r in valid),default=None))

def main():
    manifest = read(OUT/'manifest.json')
    errors,checks,rows,cost_prefix_rows,scope_rows = [],[],[],[],[]
    calls = 0
    def check(condition,message):
        checks.append(dict(valid=bool(condition),check=message))
        if not condition:
            errors.append(message)
    for item in manifest['cases']:
        state,own = read(ROOT/item['input_path']),read(ROOT/item['own_path'])
        rec = read(OUT/'cases'/(item['case_id']+'.json'))
        canonical = RepairModel(state)
        digest = canonical.digest()
        check(canonical.audit_backend()['valid'],'expected backend:'+item['case_id'])
        check(rec['expected_initial_digest']==digest,'expected digest:'+item['case_id'])
        economic = verify(state,own['common_economic_reference']['plan'])
        check(economic['valid'] and own['common_economic_reference']['optimal'],'economic reference:'+item['case_id'])
        check(set(rec['runs'])==set(LABELS),'three budgets:'+item['case_id'])
        for label,variants in rec['runs'].items():
            full = own['configurations'][label]
            full_detail = full['detail']
            cap = economic['cost']+full['summary']['epsilon']
            full_check = verify(state,full_detail['selected_plan'],cost_cap=cap)
            check(full_check['valid'],'full feasibility:'+item['case_id']+label)
            check(set(variants)==set(VARIANTS),'three variants:'+item['case_id']+label)
            # Direct prefix isolation: a saved coordination plan before the final cost solve.
            before_plan = full_detail['phases']['coordination']['plan']
            if before_plan is None:
                before_plan = full_detail['phases']['total']['plan']
            before = verify(state,before_plan,cost_cap=cap)
            check(before['valid'],'cost-prefix witness:'+item['case_id']+label)
            cost_prefix_rows.append(dict(case_id=item['case_id'],budget=label,family=own['family'],
                before_cost=before['cost'],after_cost=full_check['cost'],saving=before['cost']-full_check['cost'],
                d_change=full_check['d_vpp']-before['d_vpp'],n_change=full_check['n_adjust']-before['n_adjust'],
                total_cap=full_detail['total_cap'],
                participant_cap=full_detail['cost_tiebreak_participant_cap']))
            check(full_check['cost']<=before['cost']+2e-5,'cost-prefix nonincrease:'+item['case_id']+label)
            check(full_check['n_adjust']<=before['n_adjust'],'cost-prefix participation cap:'+item['case_id']+label)
            for variant,run in variants.items():
                tag = item['case_id']+':'+label+':'+variant
                result,row = run['result'],run['summary']
                check(run['initial_digest']==digest and result['initial_physical_digest']==digest,
                      'same physical model:'+tag)
                check(run['initial_backend_audit']['valid'],'actual backend:'+tag)
                check(result['physical_constraints_removed'] is False,'no physical constraints removed:'+tag)
                check(abs(run['cost_cap']-cap)<2e-8,'same economic cap:'+tag)
                check(result['phases']['economic'].get('reused_common_reference') is True,'reference reused:'+tag)
                check([t['objective'] for t in run['trace']]==ORDERS[variant],'exact objective removal:'+tag)
                check(result['declared_objective_order']==dict(
                    without_plan=['participation','cost'],without_coordination=['total','cost'],
                    without_final_cost=['total','participation'])[variant],'declared order:'+tag)
                calls += len(run['trace'])
                expected_phase_names = dict(without_plan={'economic','coordination','cost_tiebreak'},
                    without_coordination={'economic','total','cost_tiebreak'},
                    without_final_cost={'economic','total','coordination'})[variant]
                check(set(result['phases'])==expected_phase_names,'phase set:'+tag)
                check(all(abs(t['phase_constraints']['economic_cap']['upper']-cap)<2e-8
                          for t in run['trace'][:1]),'initial repair budget:'+tag)
                for phase_name,phase in result['phases'].items():
                    if phase_name=='economic':
                        continue
                    if phase.get('plan') is not None:
                        actual = verify(state,phase['plan'],cost_cap=cap,
                            total_cap=result.get('total_cap') if phase_name!='total' else None)
                        check(actual['valid'],'phase physical plan:'+tag+phase_name)
                        metric = actual[dict(total='d_vpp',coordination='n_adjust',cost_tiebreak='cost')[phase_name]]
                        if phase['optimal']:
                            check(abs(phase['objective']-metric)<2e-5,'phase objective:'+tag+phase_name)
                if result.get('selected_plan') is None:
                    check(not row['valid'] and result['status'].startswith('UNKNOWN'),'honest missing plan:'+tag)
                    rows.append(row)
                    continue
                actual = verify(state,result['selected_plan'],cost_cap=cap,
                    total_cap=result.get('total_cap'),
                    participant_cap=result.get('final_scope_cap'))
                check(actual['valid'],'final plan and retained caps:'+tag)
                for prefix,measured in (('full',full_check),('ablated',actual)):
                    for field,key in (('cost','cost'),('d','d_vpp'),('n','n_adjust')):
                        check(abs(row[prefix+'_'+field]-measured[key])<2e-6,'independent metric:'+tag+prefix+field)
                for field in ('d','n','cost'):
                    check(abs(row[field+'_difference']-row['full_'+field]+row['ablated_'+field])<2e-6,
                          'paired difference:'+tag+field)
                check(abs(row['solver_seconds']-sum(t['solver_seconds'] for t in run['trace']))<1e-7,
                      'all phase time counted:'+tag)
                changes_full,changes_ablated = control_changes(state,full_detail['selected_plan']),control_changes(state,result['selected_plan'])
                check(sum(v>2e-7 for v in changes_full.values())==full_check['n_adjust'],'full actual participation:'+tag)
                check(sum(v>2e-7 for v in changes_ablated.values())==actual['n_adjust'],'ablated actual participation:'+tag)
                scope_rows.append(dict(case_id=item['case_id'],budget=label,variant=variant,
                    full_controls=changes_full,ablated_controls=changes_ablated,
                    full_d=full_check['d_vpp'],ablated_d=actual['d_vpp'],event_energy=row['event_energy']))
                rows.append(row)
    check(len(rows)==540,'540 ablation configurations')
    check(calls==1080 and calls==read(OUT/'completion.json')['new_solver_calls'],'1080 actual new solves')
    for path,digest in manifest['protected_sha256'].items():
        check(sha(ROOT/path)==digest,'preserved:'+path)
    check(sha(ROOT/'run_formal_ablations.py')==manifest['runner_sha256'],'runner frozen')
    sensitivity = {}
    for variant in VARIANTS:
        subset = [r for r in scope_rows if r['variant']==variant]
        sensitivity[variant] = {}
        for threshold in (2e-7,1e-4,1e-3,.01,.1):
            pairs = [(sum(v>threshold for v in r['full_controls'].values()),
                      sum(v>threshold for v in r['ablated_controls'].values())) for r in subset]
            sensitivity[variant][str(threshold)] = dict(full_lower=sum(a<b for a,b in pairs),
                tied=sum(a==b for a,b in pairs),full_higher=sum(a>b for a,b in pairs))
    prefix_analysis = dict(configurations=len(cost_prefix_rows),
        saved=sum(r['saving']>2e-5 for r in cost_prefix_rows),
        practical_saved_over_001_cny=sum(r['saving']>.01 for r in cost_prefix_rows),
        mean_saving=mean(r['saving'] for r in cost_prefix_rows),
        max_saving=max(r['saving'] for r in cost_prefix_rows),
        max_abs_d_change=max(abs(r['d_change']) for r in cost_prefix_rows),
        n_increased=sum(r['n_change']>0 for r in cost_prefix_rows),
        optimization_calls=0,source='saved full-method coordination and final selected plans; identical preceding prefix')
    analysis = dict(by_variant={v:summarize([r for r in rows if r['variant']==v]) for v in VARIANTS},
        by_variant_budget={v:{b:summarize([r for r in rows if r['variant']==v and r['budget']==b])
                             for b in LABELS} for v in VARIANTS},
        by_variant_family={v:{f:summarize([r for r in rows if r['variant']==v and r['family']==f])
                             for f in sorted({r['family'] for r in rows})} for v in VARIANTS},
        control_threshold_sensitivity=sensitivity,direct_cost_prefix=prefix_analysis,
        attribution='No-plan changes the first priority, so D/N/C tradeoffs are expected. '
            'No-coordination retains D then minimizes C, so compare N with retained D. '
            'No-final-cost retains D/N, compare C and verify prefix evidence. '
            'Rerun ties can produce different intermediate plans; direct saved-prefix evidence '
            'isolates the cost stage without relying on identical solver tie selection.',
        metric_scope='D is external total published-power modification, N is actual non-event '
            'control participation, not communication count; budgets are repeated measures.')
    write(OUT/'analysis.json',analysis)
    write(OUT/'cost_prefix_evidence.json',cost_prefix_rows)
    write(OUT/'control_change_sensitivity.json',scope_rows)
    report = dict(valid=not errors,checks=len(checks),errors=errors,details=checks,no_new_optimization_calls=True)
    write(OUT/'audit.json',report)
    lines = ['# 正式模块消融','',
        '60个案例×3档共同预算×3种消融＝540组，新求解1080次；完整方法180组和共同经济参照均复用已保存结果。',
        '数据、物理约束、整数模式、发布计划、事件状态与核心方法不变，仅移除相应目标阶段。',
        '去计划保持：N→C；去协调择优：D→C；去最终成本：D→N。完整方法为D→N→C，所有配置均受共同经济预算约束。','',
        f"审计{'通过' if report['valid'] else '未通过'}，检查{len(checks)}项。",'',
        '|消融|完整/消融平均D(kWh)|完整/消融平均N|完整−消融平均成本(元)|',
        '|---|---|---|---:|']
    for variant,group in analysis['by_variant'].items():
        av = group['means']
        lines.append(f"|{variant}|{av['full_d']}/{av['ablated_d']}|{av['full_n']}/{av['ablated_n']}|"
                     f"{av['full_minus_ablated_cost']}|")
    lines += ['',f"同前缀最终成本阶段效果：{prefix_analysis}。",'',
        'analysis.json保留全部更好、相同、更差结果及按预算/场景分组结果，不将不符合预期的符号作为删例依据。',
        '工程幅度D阈值沿用max(0.01 kWh,事件电量1%)；参与范围同时按0.0001/0.001/0.01/0.1 kW检查。',
        '同前缀成本证据来自完整方法已保存的协调阶段和最终方案，不增加求解、不改变历史结果。',
        '保留阶段的指标若发生变化需结合记录的上限、最优性和求解器择解解释，不能机械地归因于被删模块。',
        '时间为实现层面描述；完整方法历史记录与消融记录开销不同，不用于直接声称算法加速。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(valid=report['valid'],checks=len(checks),errors=errors[:12],
        variants=analysis['by_variant'],cost_prefix=prefix_analysis),ensure_ascii=False),flush=True)
    require(report['valid'],'formal ablation audit failed')

if __name__=='__main__':
    main()
