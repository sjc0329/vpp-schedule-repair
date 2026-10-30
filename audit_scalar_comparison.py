"""Independent scalarization/normalization/paired outcome audit; no optimization."""
import json
from statistics import mean

from run_pilot import require,write,sha
from run_scalar_comparison import ROOT,OUT,WEIGHTS,read,LABELS
from audit_tracking_comparison import aggregate as tracking_aggregate,control_changes
from audit_tracking_comparison import practical_summary as tracking_practical_summary
from vpp_repair.baseline_objectives import scalar_objective
from vpp_repair.model import RepairModel
from vpp_repair.verify import verify

def neutral_names(value):
    if isinstance(value,dict):
        return {key.replace('tracking','baseline'):neutral_names(item) for key,item in value.items()}
    if isinstance(value,list):
        return [neutral_names(item) for item in value]
    return value

def aggregate(rows):
    return neutral_names(tracking_aggregate(rows))

def practical_summary(rows):
    return neutral_names(tracking_practical_summary(rows))

def main():
    manifest = read(OUT/'manifest.json')
    checks,errors,rows,material,tradeoffs = [],[],[],[],[]
    calls = 0
    def check(condition,message):
        checks.append(dict(valid=bool(condition),check=message))
        if not condition:
            errors.append(message)
    def inspect(state,result,cap,tag,normalization=None):
        check(result['import_audit']['valid'] and result['import_audit']['source_backend_audit']['valid'],
              'backend import:'+tag)
        actual = verify(state,result['plan'],cost_cap=cap)
        check(actual['valid'],'physics and budget:'+tag)
        cfg = result['configuration']
        if normalization is not None:
            check(cfg['weights']==WEIGHTS and cfg['normalization']==normalization,'frozen scalar objective:'+tag)
            value = scalar_objective(actual,normalization,WEIGHTS)['objective']
        else:
            field = dict(cost='cost',deviation='d_vpp',participation='n_adjust')[cfg['metric']]
            value = actual[field]
        check(value<=result['objective']+2e-5,'objective upper:'+tag)
        if result['optimal']:
            check(abs(value-result['objective'])<2e-5,'optimal objective:'+tag)
        check(result['lower_bound'] is None or result['lower_bound']<=value+2e-5,'bound:'+tag)
        for key in ('cost','d_vpp','d_edge','n_adjust'):
            check(abs(actual[key]-result['validation'][key])<2e-6,'metric:'+tag+key)
        return actual,value
    for item in manifest['cases']:
        state,own = read(ROOT/item['input_path']),read(ROOT/item['own_path'])
        rec = read(OUT/'cases'/(item['case_id']+'.json'))
        check(set(rec['runs'])==set(LABELS),'three budgets:'+item['case_id'])
        econ = verify(state,own['common_economic_reference']['plan'])
        check(econ['valid'],'economic reference:'+item['case_id'])
        for label,run in rec['runs'].items():
            tag = item['case_id']+':'+label
            row = run['summary']
            check(abs(run['cost_cap']-econ['cost']-run['epsilon'])<2e-8,'common cap:'+tag)
            check(abs(run['cost_scale']-.8*state['event']['task']['energy'])<1e-9,'frozen fallback scale:'+tag)
            expected = RepairModel(state)
            expected.set_cost_cap(run['cost_cap'])
            digest = expected.digest()
            values = {}
            for metric,result in run['anchors'].items():
                calls += 1
                check(result['import_audit']['canonical_digest']==digest,'anchor canonical model:'+tag+metric)
                actual,value = inspect(state,result,run['cost_cap'],tag+metric)
                values[metric] = dict(cost=actual['cost'],deviation=actual['d_vpp'],participation=actual['n_adjust'])
            check(len(values)==3,'three payoff anchors:'+tag)
            for role,seed in run['seed_audits'].items():
                check(seed.get('valid') and seed.get('stored') and seed.get('scip_complete_check'),
                      'accepted seed:'+tag+role)
            proposed_plan = own['configurations'][label]['detail']['selected_plan']
            proposed = verify(state,proposed_plan,cost_cap=run['cost_cap'])
            check(proposed['valid'],'own feasibility:'+tag)
            if 'normalization' not in run:
                check('normalization_error' in run and 'result' not in run,'honest normalization failure:'+tag)
                rows.append(row)
                continue
            check(all(r['optimal'] for r in run['anchors'].values()),'optimal payoff anchors:'+tag)
            for metric,entry in run['normalization'].items():
                ideal = values[metric][metric]
                high = max(p[metric] for p in values.values())
                span = max(0.,high-ideal)
                fallback = dict(cost=run['cost_scale'],deviation=max(1.,state['event']['task']['energy']),
                                participation=max(1,len(state['load'])-1))[metric]
                check(abs(entry['ideal']-ideal)<2e-6 and abs(entry['observed_anchor_max']-high)<2e-6,
                      'normalization anchors:'+tag+metric)
                check(abs(entry['scale']-(span if span>1e-8 else fallback))<2e-6 and entry['scale']>0,
                      'normalization positive scale:'+tag+metric)
                check(entry['fallback_used']==(span<=1e-8) and not entry['observed_max_is_global_bound'],
                      'normalization scope:'+tag+metric)
            result = run['result']
            calls += 1
            check(result['import_audit']['canonical_digest']==digest,'scalar canonical model:'+tag)
            actual,value = inspect(state,result,run['cost_cap'],tag+'scalar',run['normalization'])
            own_value = scalar_objective(proposed,run['normalization'],WEIGHTS)['objective']
            if result['optimal']:
                check(value<=own_value+2e-5,'scalar optimum against own plan:'+tag)
            if row['own_total_optimal']:
                check(proposed['d_vpp']<=actual['d_vpp']+2e-5,'own D optimum against scalar:'+tag)
            for prefix,measured in (('own',proposed),('baseline',actual)):
                for field,key in (('cost','cost'),('d','d_vpp'),('n','n_adjust')):
                    check(abs(row[prefix+'_'+field]-measured[key])<2e-6,'paired metric:'+tag+prefix+field)
            for field in ('d','n','cost'):
                check(abs(row[field+'_difference']-row['own_'+field]+row['baseline_'+field])<2e-6,
                      'paired difference:'+tag+field)
            check(row['anchor_solver_calls']==3 and row['scalar_solver_calls']==1,'all calls charged:'+tag)
            check(abs(row['baseline_solver_seconds']-row['anchor_solver_seconds']-row['scalar_solver_seconds'])<1e-7,
                  'all solver time charged:'+tag)
            rows.append(row)
            material_row = dict(row,own_control_changes=control_changes(state,proposed_plan),
                                baseline_control_changes=control_changes(state,result['plan']))
            for prefix in ('own','baseline'):
                check(sum(v>2e-7 for v in material_row[prefix+'_control_changes'].values())==row[prefix+'_n'],
                      'participation independent controls:'+tag+prefix)
            material.append(material_row)
            tradeoffs.append(dict(case_id=item['case_id'],budget=label,scalar_optimal=result['optimal'],
                scalar_criterion_baseline=value,scalar_criterion_own=own_value,
                normalized_baseline=scalar_objective(actual,run['normalization'],WEIGHTS),
                normalized_own=scalar_objective(proposed,run['normalization'],WEIGHTS)))
    for path,digest in manifest['protected_sha256'].items():
        check(sha(ROOT/path)==digest,'preserved:'+path)
    check(sha(ROOT/'run_scalar_comparison.py')==manifest['runner_sha256'],'runner frozen')
    check(len(rows)==180,'180 pairs')
    check(calls==read(OUT/'completion.json')['solver_calls'],'actual calls accounted')
    def practical(subset):
        return practical_summary(subset) if subset else dict(configurations=0)
    analysis = dict(all=aggregate(rows),
        by_budget={label:aggregate([r for r in rows if r['budget']==label]) for label in LABELS},
        by_family={f:aggregate([r for r in rows if r['family']==f]) for f in sorted({r['family'] for r in rows})},
        practical=dict(all=practical(material),
            optimal_only=practical([r for r in material if r['baseline_optimal']]),
            by_budget={label:practical([r for r in material if r['budget']==label]) for label in LABELS},
            by_family={f:practical([r for r in material if r['family']==f]) for f in sorted({r['family'] for r in rows})}),
        total_solver_calls=calls,payoff_solver_calls=sum(r['anchor_solver_calls'] for r in rows),
        joint_solver_calls=sum(r['scalar_solver_calls'] for r in rows),
        payoff_solver_seconds=sum(r['anchor_solver_seconds'] for r in rows),
        joint_solver_seconds=sum(r['scalar_solver_seconds'] or 0 for r in rows),
        scalar_criterion_better_than_own=sum(r['scalar_criterion_baseline']<r['scalar_criterion_own']-2e-5 for r in tradeoffs),
        interpretation='Designed related cases and repeated budgets, not independent population samples. '
            'Keep frozen S5 regardless of outcome. All payoff costs included. C,D,N tradeoffs are '
            'reported separately; different native criteria and different solvers do not imply universal superiority.')
    write(OUT/'analysis.json',analysis)
    write(OUT/'scalar_tradeoffs.json',tradeoffs)
    write(OUT/'control_change_sensitivity.json',material)
    report = dict(valid=not errors,checks=len(checks),errors=errors,details=checks,no_optimization_calls=True)
    write(OUT/'audit.json',report)
    lines = ['# 第二篇文献核心机制：归一化联合目标正式对比','',
        'Koksalmis等（2025）重调度质量—稳定性归一化机制的VPP适配。成本、总计划修改、实际参与范围分别映射为C、D、N；不是跑道系统或原文启发式算法的完整复现。','',
        '60案例×3档共同预算。S5权重固定为成本0.45、修改0.45、参与0.10；每次求解20秒。每组先求3个参照点再求联合目标，参照点均受同一预算约束。',
        '每次使用核验后的共同经济参照热启动，不读取我方修复方案作为基线起点。不因结果追加调参或修改方法。','',
        f"实际调用{calls}次，其中参照点{analysis['payoff_solver_calls']}次、联合目标{analysis['joint_solver_calls']}次。",'',
        f"审计{'通过' if report['valid'] else '未通过'}：{len(checks)}项。",'',
        '|范围|配对组数|基线最优|平均D：我方/基线(kWh)|平均成本差：我方−基线(元)|',
        '|---|---:|---:|---|---:|']
    for label,group in [('全部',analysis['all'])]+list(analysis['by_budget'].items()):
        av = group['averages_all']
        lines.append(f"|{label}|{group['configurations']}|{group['baseline_optimal']}|"
            f"{av['own_mean_d'] if av else None}/{av['baseline_mean_d'] if av else None}|"
            f"{av['mean_cost_difference'] if av else None}|")
    lines += ['',f"工程幅度D比较（我方更低/相近/更高）：{analysis['practical']['all'].get('d_counts')}。",'',
        f"控制变化阈值0.01 kW时的参与范围比较：{analysis['practical']['all'].get('n_counts_by_control_threshold_kw',{}).get('0.01')}。",'',
        f"D接近时的参与范围比较：{analysis['practical']['all'].get('coordination_when_d_comparable')}。",'',
        '工程幅度D阈值为max(0.01 kWh,事件电量1%)；参与范围阈值敏感性沿用第一篇的0.0001/0.001/0.01/0.1 kW，全量保留。',
        '联合目标参照最大值不是全局最坏值，零跨度使用公开正尺度兜底，归一化不截断。',
        '联合目标自身偏好下的双方方案值见scalar_tradeoffs.json；不以我方D目标值与基线联合目标值直接比较。',
        '计时包含参照点及全部建模、种子、求解开销；我方时间来自历史记录且使用不同求解器，不直接归因为算法加速。',
        '本轮没有修改数据集、原核心方法或第一篇结果，也没有运行模块消融。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(valid=report['valid'],checks=len(checks),errors=errors[:10],overall=analysis['all']),ensure_ascii=False))
    require(report['valid'],'scalar comparison audit failed')

if __name__=='__main__':
    main()
