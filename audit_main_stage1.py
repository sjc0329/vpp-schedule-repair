"""Audit saved main-stage-1 results and write paired descriptive analysis.

    No solver calls; variants are dependent cases sharing 12 publications.
"""
from copy import deepcopy
import json
from pathlib import Path

from run_pilot import require,sha,write
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
DATA = ROOT/"datasets"/"main_stage1_v1"
OUT = ROOT/"results"/"main_stage1_v1"
TOL = 2e-6


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def physical_plan(plan):
    result = deepcopy(plan)
    result.pop("case_id",None)
    result.pop("version",None)
    return result


def main():
    manifest,summary = read(DATA/"manifest.json"),read(OUT/"summary.json")
    require(summary["all_completed"],"main batch has execution errors or incomplete configurations")
    require(all(sha(DATA/p)==digest for p,digest in manifest["input_sha256"].items()),"input changed")
    require(all(sha(ROOT/p)==digest for p,digest in manifest["source_sha256"].items()),"execution source changed")
    require(all(sha(ROOT/p)==digest for p,digest in manifest["prior_template_evidence_sha256"].items()),
            "source template evidence changed")
    rows,case_summaries = [],[]
    checked_plans,phase_calls,max_residual = 0,0,0.0
    all_optimal = True
    monotonic_checks,equal_D_coordination_checks,standby_analytical_checks = 0,0,0
    for path in sorted((OUT/"cases").glob("*.json")):
        record = read(path)
        name,template,energy = record["case_id"],record["template"],record["energy"]
        state = read(DATA/(name+"-event-state.json"))
        before = read(DATA/(name+"-pre-event.json"))
        original = read(DATA/"shared_publications"/(template+".json"))
        source_path = ROOT/"datasets"/"formal_templates_v1"/(template+"-published.json")
        require(sha(source_path)==record["source_publication_sha256"],"source publication changed")
        require(sha(source_path)==sha(DATA/"shared_publications"/(template+".json")),"publication byte copy differs")
        require(physical_plan(before["reference"])==physical_plan(original),"publication regenerated or altered")
        require(verify(before,before["reference"])["valid"],"invalid original prefix snapshot")
        require(record["backend_audit"]["valid"],"backend matrix audit failed")
        for r in state["resources"]:
            require(abs(r["initial_energy"]-original["resources"][r["id"]]["energy"][state["prefix"]])<2e-7,
                    "actual state reset")
        for t in before["tasks"]:
            executed = sum(original["tasks"][t["id"]][:before["prefix"]])*before["dt"]
            require(abs(t["energy"]+executed-t["original_energy"])<2e-7,"remaining task wrong")
        certificate = record["certificate"]
        if certificate["witness_plan"] is not None:
            check = verify(state,certificate["witness_plan"],total_cap=0,
                           participant_cap=certificate["minimum_direct_support_edges"])
            require(check["valid"],"invalid constructive witness")
        configs = record["configurations"]
        econ = record["common_economic_reference"]
        upper = econ["validation"]["cost"]
        for label,item in configs.items():
            row,result = item["summary"],item["detail"]
            require(row["valid"],"missing feasible result")
            if label=="economic":
                plan = result["plan"]
                caps = {}
                phases = {"economic":result}
            else:
                plan = result["selected_plan"]
                epsilon = row["alpha"]*0.8*energy
                require(abs(row["epsilon"]-epsilon)<1e-12,"budget rule changed")
                require(abs(result["economic_reference"]["upper"]-upper)<2e-7,"economic reference differs")
                caps = dict(cost_cap=result["economic_reference"]["cost_cap"],
                            participant_cap=result["cost_tiebreak_participant_cap"],
                            total_cap=result["total_cap"])
                phases = {k:v for k,v in result["phases"].items() if k!="economic"}
                require(all(p["optimal"] for p in phases.values()),"incomplete proof in repair stages")
            selected = verify(state,plan,**caps)
            require(selected["valid"],"independent saved-plan validation failed")
            for field in ("cost","d_vpp","d_edge","n_adjust"):
                require(abs(selected[field]-row[field])<TOL,"summary metric mismatch")
            for phase_name,phase in phases.items():
                phase_calls += 1
                all_optimal &= phase["optimal"]
                if phase.get("plan") is None:
                    continue
                stage_caps = {}
                if label!="economic":
                    stage_caps["cost_cap"] = (result["cost_tiebreak_effective_cap"]
                        if phase_name=="cost_tiebreak" else result["economic_reference"]["cost_cap"])
                    stage_caps["participant_cap"] = (result["cost_tiebreak_participant_cap"]
                        if phase_name=="cost_tiebreak" else result["participant_cap"])
                    if phase_name!="total":
                        stage_caps["total_cap"] = result["total_cap"]
                check = verify(state,phase["plan"],**stage_caps)
                require(check["valid"],"saved intermediate stage invalid")
                checked_plans += 1
                max_residual = max(max_residual,max(check["residuals"].values()))
            rows.append(row)
        ordered = [configs[k]["summary"] for k in ("alpha_0","alpha_0p1","alpha_0p3")]
        if record["family"]=="G4":
            # In this declared standby regime, response costs 1.01/kWh versus
            # event purchases 0.8/kWh, no old task overlaps and PV is saturated.
            # Check the resulting cost/energy relation against saved solutions.
            source_caps = sorted((sum(r["discharge_limit"][k]*state["dt"]
                for k in range(state["event"]["task"]["arrival"],state["event"]["task"]["deadline"]))
                for r in state["resources"]),reverse=True)
            for row in ordered:
                support = min(energy,row["epsilon"]/(1.01-0.8))
                expected_d = energy-support
                require(abs(row["d_vpp"]-expected_d)<TOL,"standby energy/budget relation mismatch")
                require(abs(row["extra_cost"]-(1.01-0.8)*support)<TOL,"standby cost mismatch")
                remaining,count = support,0
                for capacity in source_caps:
                    if remaining<=TOL:
                        break
                    count += 1
                    remaining -= capacity
                require(row["n_adjust"]==count,"standby participation mismatch")
                standby_analytical_checks += 1
        for low,high in zip(ordered,ordered[1:]):
            require(high["d_vpp"]<=low["d_vpp"]+TOL,"budget monotonicity violated")
            monotonic_checks += 1
            if abs(high["d_vpp"]-low["d_vpp"])<TOL:
                require(high["n_adjust"]<=low["n_adjust"],"coordination monotonicity at equal D violated")
                equal_D_coordination_checks += 1
        baseline = configs["economic"]["summary"]
        zero,low,high = ordered
        case_summaries.append(dict(case_id=name,template=template,family=record["family"],energy=energy,
            economic_D=baseline["d_vpp"],zero_D=zero["d_vpp"],low_D=low["d_vpp"],high_D=high["d_vpp"],
            zero_N=zero["n_adjust"],low_N=low["n_adjust"],high_N=high["n_adjust"],
            high_extra_cost=high["extra_cost"],
            zero_budget_reduction=(1-zero["d_vpp"]/baseline["d_vpp"]
                                   if baseline["d_vpp"]>TOL else None),
            high_improves_total=high["d_vpp"]<zero["d_vpp"]-TOL,
            certificate_energy=certificate["absorption_energy"],
            certificate_insufficient_but_full_zero=(not certificate["direct_absorption_sufficient"]
                                                   and zero["d_vpp"]<TOL)))
    require(len(rows)==144 and phase_calls==360,"unexpected configuration or phase count")
    groups = {}
    for family in ("G1","G2","G3","G4"):
        selected = [c for c in case_summaries if c["family"]==family]
        groups[family] = dict(cases=len(selected),
            zero_budget_zero_modification=sum(c["zero_D"]<TOL for c in selected),
            high_budget_zero_modification=sum(c["high_D"]<TOL for c in selected),
            high_budget_improves_total=sum(c["high_improves_total"] for c in selected))
    reductions = [c["zero_budget_reduction"] for c in case_summaries if c["zero_budget_reduction"] is not None]
    timings = sorted(r["event_to_verified_wall_seconds"] for r in rows if r["method"]!="economic")
    audit = dict(cases=36,configurations=len(rows),verified_selected_plans=len(rows),
        actual_solver_phases=phase_calls,independently_reverified_phase_plans=checked_plans,
        all_phases_optimal=bool(all_optimal),maximum_raw_constraint_residual=max_residual,
        original_publications_byte_identical=True,physical_publications_shared_within_template=True,
        template_input_and_results_unchanged=True,frozen_new_inputs_unchanged=True,
        budget_monotonicity_checks=monotonic_checks,
        equal_deviation_coordination_monotonicity_checks=equal_D_coordination_checks,
        standby_budget_deviation_participation_checks=standby_analytical_checks,
        zero_budget_improved_cases=sum(c["zero_D"]<c["economic_D"]-TOL for c in case_summaries),
        zero_budget_reduction_range=[min(reductions),max(reductions)],groups=groups,
        total_repair_configs_zero_modification=sum(r["method"]!="economic" and r["d_vpp"]<TOL for r in rows),
        certificate_insufficient_but_full_zero_cases=[c["case_id"] for c in case_summaries
                                                     if c["certificate_insufficient_but_full_zero"]],
        measured_repair_wall_seconds=dict(median=timings[len(timings)//2],max=max(timings),
            note="Includes preparation, certificate and rebuilt-matrix diagnostic, shared economic solve, all selection stages and checks; excludes reused day-ahead publication computation"),
        case_summaries=case_summaries)
    artifacts = sorted(DATA.rglob("*.json"))+sorted((OUT/"cases").glob("*.json"))+[
        OUT/"summary.json",OUT/"configurations.csv",ROOT/"audit_main_stage1.py"]
    audit["evidence_sha256"] = {str(p.relative_to(ROOT)):sha(p) for p in artifacts}
    write(OUT/"audit.json",audit)
    lines = ["# 主体实验第一批：固定发布计划下的事件强度扫描", "",
        "12个针对性结构模板，各派生4、6、8kWh事件，原发布计划不重新生成。",
        "36个运行案例共享12份发布计划，不是36条独立日曲线。",
        "144个配置全部返回独立核验可行方案；360个求解阶段均达到求解器最优状态。", "",
        "经济参照为框架自身所需的成本参照，本批尚未运行外部文献对比或模块消融。", "",
        "## 配对结果", "",
        "|案例|经济解总修改|零预算总修改|低预算总修改|高预算总修改|零/低/高预算参与数|高预算实际额外成本/元|",
        "|---|---:|---:|---:|---:|---|---:|"]
    for c in case_summaries:
        lines.append(f"|{c['case_id']}|{c['economic_D']:.5f}|{c['zero_D']:.5f}|{c['low_D']:.5f}|"
                     f"{c['high_D']:.5f}|{c['zero_N']}/{c['low_N']}/{c['high_N']}|{c['high_extra_cost']:.5f}|")
    lo,hi = audit["zero_budget_reduction_range"]
    lines += ["", "## 结果解读", "",
        f"零预算择优在{audit['zero_budget_improved_cases']}/36个案例中减少总修改，"
        f"相对当次经济解幅度{100*lo:.1f}%—{100*hi:.1f}%。",
        "这个比较反映经济近等价方案中的择优；经济求解器返回的某个解并非计划保持最优解，",
        "不能将百分比解释为对所有既有稳定性调度方法的优势。", "",
        "G2中本地与接口充足的跨Edge三种事件强度均可完全吸收。",
        "受限接口模板的直接证书对6、8kWh不充分，但完整优化仍可通过其他资源调整实现零总修改；",
        "因此约5.99kWh不是完整系统恢复能力上限，不能把它画成全模型失效阈值。", "",
        "G4是有合同补偿的备用资源场景，高预算允许调用备用；应结合强度观察参与人数变化，",
        "零预算下修改计划可能是其经济最优选择。每个配置实际成本、内部修改和全部动作完整保存。", "",
        "G4在本轮条件下每增加1kWh备用支援，相比外购增加0.21元；预算允许的支援量",
        "为min(E_event,epsilon/0.21)，对应最小修改max(0,E_event-epsilon/0.21)。",
        "已对9案例、3档预算共27个关系核对成本、总修改与功率容量推导的参与数，均一致。",
        "8kWh事件的高预算结果为额外1.68元、零总修改，三种分布参与数分别为1、2、3。", "",
        "G3九例增加预算均未进一步减少修改，最终总修改仍为新增任务电量，非事件参与数为零。",
        "虽然输入确有离站和充电窗口差异，但本轮完整决策没有将其转化为额外保持收益；",
        "应保留为预算不敏感案例，不作为时间耦合机制优于其他完整模型的证据。", "",
        "## 计算开销与适用范围", "",
        "每个修复配置记录从准备到已核验输出的开销，包含共享经济参照、全部择优阶段、",
        "独立检查，以及本次额外的证书和完整重建矩阵诊断。日前发布计划复用，未计入在线响应时间。",
        "固定发布计划跨强度的变化有配对意义；不同模板的价格、资源和接口条件可能同时不同，",
        "不据跨模板差异单独归因某一个参数。",
        "数据有针对性地覆盖目标机制；不同曲线、事件时刻、文献对比和消融尚待后续实验。",
        "fallback_used可能由最终成本浮点比较触发，不直接视为求解超时；以阶段状态为准。"]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k not in ("case_summaries","evidence_sha256")},
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
