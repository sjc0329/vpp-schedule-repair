"""Independent saved-plan audit and paired descriptive analysis; no solves."""
import json
from pathlib import Path
from statistics import mean,median

from run_pilot import require,write,sha
from run_tracking_comparison import ROOT,OUT,LABELS,read
from vpp_repair.baseline_objectives import tracking_objective
from vpp_repair.model import RepairModel
from vpp_repair.verify import verify

def aggregate(rows):
    tol = 2e-5
    valid = [r for r in rows if r['baseline_valid']]
    def counts(field):
        return dict(own_lower=sum(r[field]<-tol for r in valid),
                    tied=sum(abs(r[field])<=tol for r in valid),
                    own_higher=sum(r[field]>tol for r in valid))
    def metrics(subset):
        return dict(configurations=len(subset),
            own_mean_d=mean(r['own_d'] for r in subset),tracking_mean_d=mean(r['baseline_d'] for r in subset),
            own_mean_n=mean(r['own_n'] for r in subset),tracking_mean_n=mean(r['baseline_n'] for r in subset),
            mean_cost_difference=mean(r['cost_difference'] for r in subset)) if subset else None
    proved = [r for r in valid if r['baseline_optimal']]
    return dict(configurations=len(rows),valid=len(valid),baseline_optimal=len(proved),
        d_counts=counts('d_difference'),n_counts=counts('n_difference'),cost_counts=counts('cost_difference'),
        averages_all=metrics(valid),averages_tracking_optimal=metrics(proved),
        own_d_lower_among_tracking_optimal=sum(r['d_difference']<-tol for r in proved),
        own_d_higher_among_tracking_optimal=sum(r['d_difference']>tol for r in proved),
        baseline_median_solver_seconds=median(r['baseline_solver_seconds'] for r in valid) if valid else None)

def control_changes(state,plan):
    """Maximum absolute change of an actual control per non-event Edge, in kW."""
    start,n,ref = state['prefix'],state['horizon'],state['reference']
    values = {}
    for edge in state['load']:
        if edge==state['event']['edge']:
            continue
        diffs = [abs(plan['edges'][edge]['renewable'][k]-ref['edges'][edge]['renewable'][k])
                 for k in range(start,n)]
        for resource in state['resources']:
            if resource['edge']==edge:
                rid = resource['id']
                for field in ('charge','discharge'):
                    diffs.extend(abs(plan['resources'][rid][field][k]-ref['resources'][rid][field][k])
                                 for k in range(start,n))
        for task in state['tasks']:
            if task['edge']==edge:
                old = ref['tasks'].get(task['id'],[0.0]*n)
                diffs.extend(abs(plan['tasks'][task['id']][k]-old[k]) for k in range(start,n))
        values[edge] = max(diffs,default=0.0)
    return values

def practical_summary(rows):
    d = dict(own_lower=0,tied=0,own_higher=0)
    for row in rows:
        tolerance = max(.01,.01*row['event_energy'])
        difference = row['d_difference']
        d['own_lower' if difference < -tolerance else 'own_higher' if difference > tolerance else 'tied'] += 1
    n = {}
    for threshold in (2e-7,1e-4,1e-3,.01,.1):
        own = [sum(v>threshold for v in row['own_control_changes'].values()) for row in rows]
        baseline = [sum(v>threshold for v in row['baseline_control_changes'].values()) for row in rows]
        n[str(threshold)] = dict(own_lower=sum(a<b for a,b in zip(own,baseline)),
            tied=sum(a==b for a,b in zip(own,baseline)),own_higher=sum(a>b for a,b in zip(own,baseline)),
            own_mean_n=mean(own),tracking_mean_n=mean(baseline))
    comparable = [r for r in rows if abs(r['d_difference'])<=max(.01,.01*r['event_energy'])]
    threshold = .01
    conditional = dict(cases=len(comparable),control_threshold_kw=threshold,
        own_lower=sum(sum(v>threshold for v in r['own_control_changes'].values()) <
                      sum(v>threshold for v in r['baseline_control_changes'].values()) for r in comparable),
        tied=sum(sum(v>threshold for v in r['own_control_changes'].values()) ==
                 sum(v>threshold for v in r['baseline_control_changes'].values()) for r in comparable),
        own_higher=sum(sum(v>threshold for v in r['own_control_changes'].values()) >
                      sum(v>threshold for v in r['baseline_control_changes'].values()) for r in comparable))
    return dict(configurations=len(rows),d_tolerance_rule='max(0.01 kWh, 1% of event energy)',
                d_counts=d,n_counts_by_control_threshold_kw=n,
                coordination_when_d_comparable=conditional)

def main():
    manifest = read(OUT/'manifest.json')
    checks,errors,rows,materiality_rows,tracking_rows = [],[],[],[],[]
    def check(condition,message):
        checks.append(dict(valid=bool(condition),check=message))
        if not condition:
            errors.append(message)
    for item in manifest['cases']:
        state = read(ROOT/item['input_path'])
        own = read(ROOT/item['own_path'])
        rec = read(OUT/'cases'/(item['case_id']+'.json'))
        economic = verify(state,own['common_economic_reference']['plan'])
        check(economic['valid'],'economic reference:'+item['case_id'])
        check(set(rec['runs'])==set(LABELS),'three budgets:'+item['case_id'])
        for label,run in rec['runs'].items():
            tag = item['case_id']+':'+label
            result,row = run['result'],run['summary']
            check('exception' not in result,'no exception:'+tag)
            check(abs(run['cost_cap']-economic['cost']-run['epsilon'])<2e-8,'cost cap:'+tag)
            check(run['seed_audit'].get('valid') and run['seed_audit'].get('stored')
                and run['seed_audit'].get('scip_complete_check'),'accepted economic seed:'+tag)
            expected = RepairModel(state)
            expected.set_cost_cap(run['cost_cap'])
            check(expected.digest()==result['import_audit']['canonical_digest'],'same canonical model:'+tag)
            check(result['import_audit']['valid'] and result['import_audit']['source_backend_audit']['valid'],
                  'actual backend import:'+tag)
            actual = verify(state,result['plan'],cost_cap=run['cost_cap'])
            proposed_plan = own['configurations'][label]['detail']['selected_plan']
            proposed = verify(state,proposed_plan,cost_cap=run['cost_cap'])
            check(actual['valid'] and proposed['valid'],'paired feasibility and budget:'+tag)
            cfg = result['configuration']
            check(cfg['penalty']==10 and cfg['rho']==.75 and
                  abs(cfg['cost_scale']-.8*state['event']['task']['energy'])<1e-9,'frozen T9:'+tag)
            tracking = tracking_objective(state,result['plan'],actual['cost'],cfg['cost_scale'],10.,.75)
            own_tracking = tracking_objective(state,proposed_plan,proposed['cost'],cfg['cost_scale'],10.,.75)
            value = tracking['objective']
            check(value<=result['objective']+2e-5,'objective upper:'+tag)
            if result['optimal']:
                check(abs(value-result['objective'])<2e-5,'optimal objective:'+tag)
                check(value<=own_tracking['objective']+2e-5,'tracking optimality against own plan:'+tag)
            check(result['lower_bound'] is None or result['lower_bound']<=value+2e-5,'bound:'+tag)
            for prefix,measured in (('baseline',actual),('own',proposed)):
                for field,key in (('cost','cost'),('d','d_vpp'),('n','n_adjust')):
                    check(abs(row[prefix+'_'+field]-measured[key])<2e-6,'independent metric:'+tag+prefix+field)
            for field in ('d','n','cost'):
                check(abs(row[field+'_difference']-(row['own_'+field]-row['baseline_'+field]))<2e-6,
                      'paired difference:'+tag+field)
            # Conditional optimality implication applies to feasible competitors even if SCIP stops.
            if row['own_total_optimal']:
                check(proposed['d_vpp']<=actual['d_vpp']+2e-5,'own D optimality consistency:'+tag)
            rows.append(row)
            material = dict(row,own_control_changes=control_changes(state,proposed_plan),
                            baseline_control_changes=control_changes(state,result['plan']))
            for prefix in ('own','baseline'):
                check(sum(v>2e-7 for v in material[prefix+'_control_changes'].values())==row[prefix+'_n'],
                      'participation independent controls:'+tag+prefix)
            materiality_rows.append(material)
            tracking_rows.append(dict(case_id=item['case_id'],budget=label,family=own['family'],
                baseline_optimal=result['optimal'],baseline_objective=value,
                own_objective_under_tracking_criterion=own_tracking['objective'],
                baseline_power_squared=tracking['power_squared'],own_power_squared=own_tracking['power_squared'],
                baseline_energy_squared=tracking['energy_squared'],own_energy_squared=own_tracking['energy_squared']))
    check(len(rows)==180,'180 paired configurations')
    for path,digest in manifest['protected_sha256'].items():
        check(sha(ROOT/path)==digest,'preserved:'+path)
    check(sha(ROOT/'run_tracking_comparison.py')==manifest['runner_sha256'],'runner frozen')
    check(read(OUT/'completion.json')['solver_calls']==180,'180 actual solver calls')
    analysis = dict(all=aggregate(rows),
        by_budget={label:aggregate([r for r in rows if r['budget']==label]) for label in LABELS},
        by_family={family:aggregate([r for r in rows if r['family']==family]) for family in sorted({r['family'] for r in rows})},
        by_profile={profile:aggregate([r for r in rows if r['profile']==profile]) for profile in sorted({r['profile'] for r in rows})},
        interpretation='Descriptive paired results on designed related cases. Budget configurations are '
            'repeated measures, not 180 independent cases. Tracking and proposed methods optimize '
            'different objectives; no comparison of their raw objective values. Solver time is '
            'implementation-level and historical, not proof of algorithmic speedup.')
    analysis['practical_sensitivity'] = dict(all=practical_summary(materiality_rows),
        tracking_optimal_only=practical_summary([r for r in materiality_rows if r['baseline_optimal']]),
        by_budget={label:practical_summary([r for r in materiality_rows if r['budget']==label]) for label in LABELS},
        by_family={family:practical_summary([r for r in materiality_rows if r['family']==family])
                   for family in sorted({r['family'] for r in materiality_rows})},
        rationale='Supplementary reporting added after observing small perturbations in the first 28 '
            'formal configurations. Does not alter frozen objectives, parameters, primary metrics or data.')
    write(OUT/'control_change_sensitivity.json',materiality_rows)
    write(OUT/'tracking_tradeoffs.json',tracking_rows)
    analysis['tracking_criterion_tradeoff'] = dict(
        explanation='Both plans evaluated under the SAME tracking criterion, not comparison of '
            'heterogeneous native objectives. Baseline optimality is checked against the own-method plan.',
        own_mean_power_squared=mean(r['own_power_squared'] for r in tracking_rows),
        baseline_mean_power_squared=mean(r['baseline_power_squared'] for r in tracking_rows),
        own_mean_energy_squared=mean(r['own_energy_squared'] for r in tracking_rows),
        baseline_mean_energy_squared=mean(r['baseline_energy_squared'] for r in tracking_rows),
        tracking_objective_better_than_own=sum(r['baseline_objective']<r['own_objective_under_tracking_criterion']-2e-5
                                             for r in tracking_rows))
    write(OUT/'analysis.json',analysis)
    report = dict(valid=not errors,checks=len(checks),errors=errors,details=checks,no_new_optimization_calls=True)
    write(OUT/'audit.json',report)
    lines = ['# 第一篇文献核心机制：正式预算受控适配对比','',
        'Naughton等（IEEE TPWRS，2021）功率—状态平方跟踪机制，在共同VPP物理模型上的适配；不是原文完整系统复现。','',
        '60个固定案例×3档共同经济预算＝180组配对。参数λ=10、ρ=0.75，尺度0.8×事件电量；单次SCIP求解时限20秒。',
        '双方共用原经济基准和成本上限。跟踪基线接收完整核验的经济基准热启动，不接收我们方法的修复计划。',
        '正式协议相对校准增加了共同硬预算与热启动；参数没有重新搜索。', '',
        f"独立审计：{'通过' if report['valid'] else '未通过'}，检查项{len(checks)}个。",'',
        '|范围|组数|基线最优|我方D更低/相同/更高|我方N更低/相同/更高|平均成本差（我方−基线）|',
        '|---|---:|---:|---|---|---:|']
    for label,group in [('全部',analysis['all'])]+list(analysis['by_budget'].items()):
        d,n = group['d_counts'],group['n_counts']
        av = group['averages_all']
        lines.append(f"|{label}|{group['configurations']}|{group['baseline_optimal']}|"
            f"{d['own_lower']}/{d['tied']}/{d['own_higher']}|{n['own_lower']}/{n['tied']}/{n['own_higher']}|"
            f"{av['mean_cost_difference'] if av else None}|")
    practical = analysis['practical_sensitivity']['all']
    proved_practical = analysis['practical_sensitivity']['tracking_optimal_only']
    strict = analysis['all']
    track = analysis['tracking_criterion_tradeoff']
    lines += ['',f"工程幅度口径下的D比较（我方更低/接近/更高）：{practical['d_counts']}。",'',
        f"仅保留基线证明最优组：{proved_practical['configurations']}组，工程幅度D比较为{proved_practical['d_counts']}。",'',
        f"控制变化阈值0.01 kW时，参与范围比较：{practical['n_counts_by_control_threshold_kw']['0.01']}。",'',
        f"D工程幅度接近时的参与范围比较：{practical['coordination_when_d_comparable']}。",'',
        f"平均D（我方/基线）：{strict['averages_all']['own_mean_d']} / {strict['averages_all']['tracking_mean_d']} kWh。",'',
        f"仅基线最优组的平均D（我方/基线）：{strict['averages_tracking_optimal']['own_mean_d']} / {strict['averages_tracking_optimal']['tracking_mean_d']} kWh。",'',
        f"平均归一化功率平方偏差（我方/基线）：{track['own_mean_power_squared']} / {track['baseline_mean_power_squared']}。",'',
        f"平均归一化状态平方偏差（我方/基线）：{track['own_mean_energy_squared']} / {track['baseline_mean_energy_squared']}。"]
    lines += ['', 'analysis.json另外报告仅基线证明最优子集，以及预算、家族和曲线分组结果。',
        '补充工程幅度检查：D以max(0.01 kWh,事件电量的1%)为相近阈值；实际控制变化以0.0001、0.001、0.01、0.1 kW阈值报告参与范围敏感性。',
        '该补充源于前28组出现微小控制扰动；保留原严格指标，不改变模型或参数，也不只选择有利阈值。',
        '计划修改、参与范围和成本分别报告，不要求我方全部指标占优；不比较异质目标的原始目标值。',
        'tracking_tradeoffs.json将两种方案放入同一跟踪准则，另外报告功率/状态平方偏差，保留对比方法擅长的指标。',
        '我方直接最小化总计划修改，D优势应解释为所声明偏好与框架目标的效果，而非所有目标下的通用优势。',
        '计时来自不同求解器及历史运行，仅作实现层面描述，不直接归因为框架算法加速。',
        '本轮未运行第二篇文献基线或正式模块消融，也未修改原有数据或核心方法。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(valid=report['valid'],checks=len(checks),errors=errors[:10],overall=analysis['all']),ensure_ascii=False))
    require(report['valid'],'formal tracking comparison audit failed')

if __name__=='__main__':
    main()
