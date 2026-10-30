"""Independent saved-plan audit; performs no optimization calls."""
import json
from pathlib import Path

from run_pilot import require,write,sha
from run_baseline_calibration import OUT,ROOT,fingerprint
from vpp_repair.verify import verify
from vpp_repair.baseline_objectives import tracking_objective,scalar_objective

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def main():
    manifest = read(OUT/'manifest.json')
    checks,errors,stopped = [],[],[]
    def check(ok,message):
        checks.append(dict(valid=bool(ok),check=message))
        if not ok:
            errors.append(message)
    evaluation = {fingerprint(read(p)) for folder in ('main_stage1_v1','main_stage2_v1')
                  for p in (ROOT/'datasets'/folder).glob('*-event-state.json')}
    seen = set()
    for case in manifest['cases']:
        state = read(ROOT/case['path'])
        digest = fingerprint(state)
        check(digest not in seen and digest not in evaluation,'disjoint:'+case['case_id'])
        seen.add(digest)
        check(sha(ROOT/case['path'])==case['input_sha256'],'input:'+case['case_id'])
        record = read(OUT/'cases'/(case['case_id']+'.json'))
        check(len(record['runs'])==19,'19 calls:'+case['case_id'])
        for label,result in record['runs'].items():
            tag = case['case_id']+':'+label
            check('exception' not in result,'no exception:'+tag)
            if result.get('plan') is None:
                check(result.get('status')=='timelimit' and not result.get('optimal')
                      and result.get('objective') is None and result.get('validation') is None,
                      'honest no-incumbent time limit:'+tag)
                stopped.append(dict(case_id=case['case_id'],parameter_id=label,
                    status=result.get('status'),absolute_gap=result.get('absolute_gap'),
                    objective=result.get('objective'),lower_bound=result.get('lower_bound'),
                    solver_seconds=result.get('solver_seconds'),valid=False,has_plan=False))
                continue
            actual = verify(state,result['plan'])
            check(actual['valid'],'physics:'+tag)
            for metric in ('cost','d_vpp','d_edge','n_adjust'):
                check(abs(actual[metric]-result['validation'][metric])<2e-6,'metric:'+tag+':'+metric)
            check(result['import_audit']['valid'] and
                  result['import_audit']['source_backend_audit']['valid'],'import:'+tag)
            cfg = result['configuration']
            if cfg['kind']=='tracking':
                value = tracking_objective(state,result['plan'],actual['cost'],cfg['cost_scale'],
                    cfg['penalty'],cfg['rho'])['objective']
            elif cfg['kind']=='scalar':
                value = scalar_objective(actual,cfg['normalization'],cfg['weights'])['objective']
            else:
                value = actual[dict(cost='cost',deviation='d_vpp',participation='n_adjust')[cfg['metric']]]
            # A stopped solve can retain slack epigraphs; equality is required for optimal solves.
            check(value<=result['objective']+2e-5,'objective upper:'+tag)
            if result['optimal']:
                check(abs(value-result['objective'])<2e-5,'optimal objective:'+tag)
            else:
                stopped.append(dict(case_id=case['case_id'],parameter_id=label,
                    status=result['status'],absolute_gap=result['absolute_gap'],
                    objective=result['objective'],lower_bound=result['lower_bound'],
                    solver_seconds=result['solver_seconds'],valid=actual['valid']))
            check(result['lower_bound'] is None or result['lower_bound']<=value+2e-5,'bound:'+tag)
        norm = record.get('normalization',{})
        for metric,entry in norm.items():
            field = dict(cost='cost',deviation='d_vpp',participation='n_adjust')[metric]
            values = [record['runs']['anchor_'+m]['validation'][field]
                      for m in ('cost','deviation','participation')]
            ideal = record['runs']['anchor_'+metric]['validation'][field]
            check(abs(ideal-entry['ideal'])<2e-6,'normalization ideal:'+case['case_id']+metric)
            check(abs(max(values)-entry['observed_anchor_max'])<2e-6,'normalization max:'+case['case_id']+metric)
            check(entry['scale']>0,'positive scale:'+case['case_id']+metric)
    check(len(seen)==13,'13 unique calibration cases')
    for path,digest in manifest['protected_sha256'].items():
        check(sha(ROOT/path)==digest,'preserved:'+path)
    check(sha(ROOT/'run_baseline_calibration.py')==manifest['runner_sha256'],'runner frozen before solving')
    summary = read(OUT/'summary.json')
    check(summary['solver_calls']==247,'247 actual optimization calls')
    candidates = {}
    for kind,rows in summary['rankings'].items():
        row = rows[0]
        selected = []
        for case in manifest['cases']:
            rec = read(OUT/'cases'/(case['case_id']+'.json'))
            result = rec['runs'][row['parameter_id']]
            selected.append(dict(case_id=case['case_id'],status=result.get('status'),
                has_valid_plan=bool((result.get('validation') or {}).get('valid')),
                absolute_gap=result.get('absolute_gap'),objective=result.get('objective'),
                lower_bound=result.get('lower_bound')))
        candidates[kind] = dict(parameter_id=row['parameter_id'],configuration=row['configuration'],
            valid_cases=row['valid'],optimal_cases=row['optimal'],case_statuses=selected,
            scope='Selected under the frozen 20-second calibration protocol; '
                  'not a guarantee of globally best parameters or exact solutions for all cases.')
    write(OUT/'parameter_candidates.json',dict(candidates=candidates,
        manifest_sha256=sha(OUT/'manifest.json'),formal_evaluation_performed=False,
        recommended_solver_limit_seconds=20,cost_scale_rule='.8*event_energy'))
    report = dict(valid=not errors,checks=len(checks),errors=errors,details=checks,
                  stopped_results=stopped,
                  no_optimization_performed=True)
    write(OUT/'audit.json',report)
    lines = ['# 基线参数校准结果','',
        f"13个校准案例，{summary['solver_calls']}次求解；{summary['valid']}个物理可行结果，"
        f"{summary['optimal']}次证明最优。独立审核：{'通过' if report['valid'] else '未通过'}。",'',
        '校准集排除TIME-3（重复）和ABS-LOCAL-3（与正式集重合）。正式集仅用于输入重合排除，不用于参数选择。',
        '', '选择规则在求解前写入manifest：先比较13例中满足参考经济预算的数量，再比较等家族权重的归一化计划修改、参与范围、额外成本。',
        '参考预算为0.3×0.8×事件电量；两类基线本次均不加硬成本上限。这是参数选择偏好，不意味着基线自带经济保证。',
        '', '|基线|参数|预算内案例|最优数|平均归一化修改|平均归一化参与|平均归一化额外成本|',
        '|---|---|---:|---:|---:|---:|---:|']
    for kind,rows in summary['rankings'].items():
        for row in rows:
            lines.append(f"|{kind}|{row['parameter_id']} {row['configuration']}|"
                f"{row['within_reference_budget']}/13|{row['optimal']}/13|"
                f"{row['family_balanced_d']}|{row['family_balanced_n']}|{row['family_balanced_extra']}|")
    lines += ['',f"候选推荐：{summary['recommendations']}；状态：{summary['recommendation_status']}。",'',
        f"未证明最优的结果共{len(stopped)}个；状态、绝对界差与物理核验保存在audit.json的stopped_results。",'',
        'parameter_candidates.json记录选中参数、每例状态及证据哈希；含时限结果时只能作为该时限协议下的候选，不能声称全部案例精确求优。', '',
        '这里只报告预先声明规则下的校准结果，不等同于正式60例上的方法优劣。全部参数与失败结果均保留。',
        '本轮没有执行正式对比或模块消融，没有修改既有数据集、核心方法或历史结果。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(valid=report['valid'],checks=report['checks'],errors=errors[:10]),ensure_ascii=False))
    require(report['valid'],'calibration audit failed')

if __name__=='__main__':
    main()
