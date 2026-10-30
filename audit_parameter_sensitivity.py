"""Independent sensitivity audit and regime-specific parameter interpretation."""
import json
from statistics import mean

from run_pilot import require,write,sha
from run_parameter_sensitivity import ROOT,DATA,OUT,ALPHAS,PRICES,read
from vpp_repair.verify import verify

def prediction(state,alpha,price):
    task=state['event']['task'];slots=range(task['arrival'],task['deadline']);dt=state['dt'];ref=state['reference']
    E=task['energy'];X=sum(ref['grid']['sell'][k]*dt for k in slots)
    buy={state['buy_price'][k] for k in slots};sell={state['sell_price'][k] for k in slots}
    require(len(buy)==len(sell)==1,'prediction needs common event prices')
    pb,ps=next(iter(buy)),next(iter(sell));eps=alpha*.8*E;h0=E-X
    require(price>pb and 0<=X<=E,'prediction regime violated')
    h=eps/(price-pb) if eps<=(price-pb)*h0 else (eps+(pb-ps)*h0)/(price-ps)
    h=min(E,max(0.,h));full_extra=(price-pb)*E+(pb-ps)*X
    return dict(support=h,d=E-h,original_export=X,full_support_extra=full_extra,
                full_support_alpha=full_extra/(.8*E))

def main():
    manifest=read(OUT/'manifest.json');errors=[];checks=[];rows=[];frontiers=[];price_rows=[];calls=0
    def check(ok,message):
        checks.append(dict(valid=bool(ok),check=message))
        if not ok:errors.append(message)
    for item in manifest['cases']:
        state=read(ROOT/item['input_path']);rec=read(OUT/'cases'/(item['case_id']+'.json'))
        economic=rec['common_economic_reference'];econ=verify(state,economic['plan'])
        check(economic['optimal'] and econ['valid'],'economic reference:'+item['case_id'])
        check(economic['lower_bound']<=econ['cost']+2e-7,'reference bound:'+item['case_id'])
        calls+=len(rec['solve_trace'])
        if item['kind']=='price_variant':
            full=read(ROOT/item['full_input_path']);pub=read(ROOT/item['publication_path'])
            source_full=read(ROOT/item['source_full_input']);source_pub=read(ROOT/item['source_publication'])
            fixed=verify(full,pub);nominal=rec['nominal_check']['result'];nv=verify(full,nominal['plan'])
            check(nominal['optimal'] and fixed['valid'] and nv['valid'] and
                  abs(nv['cost']-fixed['cost'])<2e-6,'fixed publication remains optimal:'+item['case_id'])
            pub_without_id={k:v for k,v in pub.items() if k!='case_id'}
            source_without_id={k:v for k,v in source_pub.items() if k!='case_id'}
            check(pub_without_id==source_without_id,'physical publication unchanged:'+item['case_id'])
            check(all(r['wear']==item['price'] for r in full['resources']),'frozen response price:'+item['case_id'])
            for key in ('load','pv','wind','buy_price','sell_price','import_limit','export_limit','interface_limit','tasks'):
                check(full[key]==source_full[key],'other planning input unchanged:'+item['case_id']+key)
            for new,old in zip(full['resources'],source_full['resources']):
                check({k:v for k,v in new.items() if k!='wear'}=={k:v for k,v in old.items() if k!='wear'},
                      'physical resource unchanged:'+item['case_id']+new['id'])
            check(rec['backend_audit']['valid'],'actual backend:'+item['case_id'])
        case_rows=[]
        for alpha in ALPHAS:
            run=rec['configurations'][str(alpha)];detail=run['detail'];row=run['summary'];eps=alpha*.8*8
            actual=verify(state,detail['selected_plan'],cost_cap=econ['cost']+eps,
                          total_cap=detail['total_cap'],participant_cap=detail['cost_tiebreak_participant_cap'])
            check(actual['valid'],'final physics/budget/retained caps:'+item['case_id']+str(alpha))
            check(abs(row['epsilon']-eps)<1e-9,'budget rule:'+item['case_id']+str(alpha))
            for field in ('cost','d_vpp','d_edge','n_adjust'):
                check(abs(row[field]-actual[field])<2e-6,'independent metric:'+item['case_id']+str(alpha)+field)
            for phase_name,phase in detail['phases'].items():
                check(phase['optimal'],'phase optimum:'+item['case_id']+str(alpha)+phase_name)
                if phase_name=='economic':continue
                pv=verify(state,phase['plan'],cost_cap=econ['cost']+eps,
                          total_cap=detail['total_cap'] if phase_name!='total' else None)
                check(pv['valid'],'phase physics:'+item['case_id']+str(alpha)+phase_name)
                metric=pv['d_vpp'] if phase_name=='total' else pv['n_adjust'] if phase_name=='coordination' else pv['cost']
                check(abs(metric-phase['objective'])<2e-5,'phase objective:'+item['case_id']+str(alpha)+phase_name)
            if row['reused']:
                old=read(ROOT/item['source_result_path'])
                key={0.:'alpha_0',.1:'alpha_0p1',.3:'alpha_0p3'}[alpha]
                check(detail==old['configurations'][key]['detail'],'original result exactly reused:'+item['case_id']+str(alpha))
            case_rows.append(row);rows.append(row)
            if item['family']=='G4':
                pred=prediction(state,alpha,item['price'])
                # Fixed compensated-standby subclass prediction, independently compared with the full optimum.
                check(abs(actual['d_vpp']-pred['d'])<2e-5,'standby budget-price prediction:'+item['case_id']+str(alpha))
                price_rows.append(dict(case_id=item['case_id'],profile=item['profile'],price=item['price'],
                    alpha=alpha,epsilon=eps,predicted_d=pred['d'],measured_d=actual['d_vpp'],
                    predicted_full_alpha=pred['full_support_alpha'],
                    predicted_full_extra_cost=pred['full_support_extra'],n_adjust=actual['n_adjust']))
        for lo,hi in zip(case_rows,case_rows[1:]):
            check(hi['d_vpp']<=lo['d_vpp']+2e-5,'budget D monotonicity:'+item['case_id']+str(hi['alpha']))
        best=min(r['d_vpp'] for r in case_rows);first_best=next(r for r in case_rows if r['d_vpp']<=best+2e-5)
        zeros=[r for r in case_rows if r['d_vpp']<=2e-5]
        frontiers.append(dict(case_id=item['case_id'],family=item['family'],profile=item['profile'],price=item['price'],
            d_at_zero=case_rows[0]['d_vpp'],observed_grid_min_d=best,
            first_observed_grid_best_alpha=first_best['alpha'],first_zero_alpha=zeros[0]['alpha'] if zeros else None,
            first_zero_actual_extra_cost=zeros[0]['extra_cost'] if zeros else None,
            interpretation='First observed grid minimum is not proof of saturation beyond alpha=.6. '
                'Zero D certifies the D lower bound, while N/C can still change with budget.'))
    check(len(rows)==341 and calls==876,'frozen configurations and actual calls')
    check(sum(r['reused'] for r in rows)==57,'57 original configurations reused')
    for path,digest in manifest['input_sha256'].items():check(sha(ROOT/path)==digest,'frozen input:'+path)
    for path,digest in manifest['protected_sha256'].items():check(sha(ROOT/path)==digest,'preserved:'+path)
    check(sha(ROOT/'run_parameter_sensitivity.py')==manifest['runner_sha256'],'runner frozen')
    price_summary=[]
    for profile in ('ORIGINAL','DAY_SURPLUS'):
        for price in PRICES:
            group=[r for r in frontiers if r['family']=='G4' and r['profile']==profile and r['price']==price]
            pred=next(r for r in price_rows if r['profile']==profile and r['price']==price)
            price_summary.append(dict(profile=profile,price=price,cases=len(group),
                analytic_full_alpha=pred['predicted_full_alpha'],analytic_full_extra_cost=pred['predicted_full_extra_cost'],
                first_zero_alphas=sorted({r['first_zero_alpha'] for r in group})))
    analysis=dict(case_frontiers=frontiers,standby_price_summary=price_summary,
        standby_formula_checks=len(price_rows),max_standby_d_error=max(abs(r['predicted_d']-r['measured_d']) for r in price_rows),
        flat_budget_original_cases=[r['case_id'] for r in frontiers if r['price'] is None and abs(r['d_at_zero']-r['observed_grid_min_d'])<=2e-5],
        interpretation='Alpha is an economic tolerance, not a universal accuracy-maximizing hyperparameter. '
            'Report actual C,D,N and regime conditions. Baseline parameters and old performance tables remain frozen.',
        new_solver_calls=calls,configurations=len(rows),new_dataset_cases=12)
    write(OUT/'analysis.json',analysis);write(OUT/'standby_formula_checks.json',price_rows)
    audit=dict(valid=not errors,checks=len(checks),errors=errors,details=checks,no_new_optimization_calls=True)
    write(OUT/'audit.json',audit)
    lines=['# 经济预算—支援价差敏感性','',
        '19个原固定案例＋12个费用派生案例，α取0/0.05/0.1/0.15/0.2/0.25/0.3/0.35/0.4/0.5/0.6。',
        '备用响应费用为1.01/1.10/1.20元/kWh；价格轴覆盖三种支援分布和原曲线/日间富余曲线。',
        '共341配置；复用57个已保存配置，新增876次求解（12次日前最优性检查、12次事件经济参考、852次修复阶段）。',
        '费用在日前已知。原计划中备用通量为零，提高其非负通量费用后原计划仍为经济最优；每个新费用案例另外求解核对，但保留原物理发布轨迹以隔离价差效应。','',
        f"独立审计：{'通过' if audit['valid'] else '未通过'}，检查{len(checks)}项。",'',
        '|曲线|响应费用|条件性完全保持预算/元|解析α阈值|网格首次D≈0的α|',
        '|---|---:|---:|---:|---|']
    for row in price_summary:
        lines.append(f"|{row['profile']}|{row['price']}|{row['analytic_full_extra_cost']:.6f}|"
                     f"{row['analytic_full_alpha']:.6f}|{row['first_zero_alphas']}|")
    lines += ['',f"备用子类预算—偏差关系核对{len(price_rows)}组，最大D误差{analysis['max_standby_d_error']} kWh。",'',
        '解析式适用于当前备用子类：设事件E=8，原窗口外售X，响应费c，购/售电价0.8/0.2，支援h的额外成本为(c−0.8)h+0.6 max(0,h−(E−X))。功率/电量充足、当前无其他额外供能且其余条件按这些模板设定。',
        '完全保持构造预算为(c−0.8)E+0.6X；报告将其与完整模型已保存最优结果逐组核对。',
        'α=.3并非统一最优值。其效果取决于支援价差和发布时的购售电状态；经济偏好由运营预算给定，选择应结合实际成本、D和N。',
        '各案例的完整成本—D—N曲线见configurations.csv；首次观测最小D和零D位置见analysis.json。有限网格内平台与全预算域饱和分开解释。',
        '原60例数据、主体/对比/消融结果及基线参数保持不变。本批为补充敏感性，不替换原性能比较。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(valid=audit['valid'],checks=len(checks),errors=errors[:10],price_summary=price_summary,
                         max_formula_error=analysis['max_standby_d_error']),ensure_ascii=False),flush=True)
    require(audit['valid'],'parameter sensitivity audit failed')

if __name__=='__main__':main()
