"""Read-only optimization evidence audit; writes analysis, never solves models."""
import json
from pathlib import Path

from run_pilot import require,sha,write
from vpp_repair.verify import verify
from qualify_formal_templates import standby_witness

ROOT = Path(__file__).resolve().parent
DATA = ROOT/"datasets"/"main_stage2_v1"
OUT = ROOT/"results"/"main_stage2_v1"
TOL = 2e-6


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    manifest,summary = read(DATA/"manifest.json"),read(OUT/"summary.json")
    require(summary["all_completed"],"batch has incomplete configurations or execution errors")
    require(all(sha(DATA/p)==d for p,d in manifest["input_sha256"].items()),"frozen input changed")
    require(all(sha(ROOT/p)==d for p,d in manifest["source_sha256"].items()),"execution source changed")
    require(all(sha(ROOT/p)==d for p,d in manifest["prior_stage1_evidence_sha256"].items()),
            "first-batch data or results changed")
    baseline_rows = read(ROOT/"results"/"main_stage1_v1"/"audit.json")["case_summaries"]
    original_results = {r["template"]:r for r in baseline_rows if r["energy"]==8}
    rows,comparisons,traces,standby_diagnostics = [],[],[],[]
    nominal_checks,selected_checks,stage_checks,witnesses = 0,0,0,0
    all_optimal,max_residual = True,0.0
    monotonicity_checks = 0
    rechecked_configs = 0
    for path in sorted((OUT/"cases").glob("*.json")):
        record = read(path)
        name = record["case_id"]
        case = read(DATA/(name+"-input.json"))
        source = read(ROOT/"datasets"/"formal_templates_v1"/(record["template"]+"-input.json"))
        state = read(DATA/(name+"-event-state.json"))
        before = read(DATA/(name+"-pre-event.json"))
        published = read(DATA/(name+"-published.json"))
        require(case["resources"]==source["resources"],"resource parameters changed")
        require(case["buy_price"]==source["buy_price"] and case["sell_price"]==source["sell_price"],
                "price parameters changed")
        require(case["tasks"]==source["tasks"],"old task parameters changed")
        for k in range(case["horizon"]):
            h = k*case["dt"]
            if case["profile"]=="DAY_SURPLUS":
                lf,pf,wf = (0.9 if 10<=h<16 else 1.0),1.1,1.0
            else:
                lf = 1.1 if 16<=h<22 else 1.0
                pf = 0.65 if 11.5<=h<12.5 else 1.0
                wf = 0.8 if h<12 else 1.2
            for edge in case["load"]:
                for field,factor in (("load",lf),("pv",pf),("wind",wf)):
                    require(abs(case[field][edge][k]-source[field][edge][k]*factor)<1e-10,
                            "profile recipe mismatch")
        require(verify(case,published)["valid"],"invalid nominal plan")
        require(published==record["nominal"]["plan"]==before["reference"]==state["reference"],
                "reference differs across stages")
        require(verify(before,published)["valid"],"snapshot invalid")
        for r in state["resources"]:
            require(abs(r["initial_energy"]-published["resources"][r["id"]]["energy"][state["prefix"]])<2e-7,
                    "actual state reset")
        require(record["backend_audit"]["valid"],"backend audit failed")
        nominal_checks += 1
        cert = record["certificate"]
        if cert["witness_plan"] is not None:
            require(verify(state,cert["witness_plan"],total_cap=0,
                           participant_cap=cert["minimum_direct_support_edges"])["valid"],
                    "constructive witness invalid")
            witnesses += 1
        for label,item in record["configurations"].items():
            row,result = item["summary"],item["detail"]
            rechecked_configs += 1
            if row["valid"]:
                plan = result["plan"] if label=="economic" else result["selected_plan"]
                caps = {} if label=="economic" else dict(
                    cost_cap=result["economic_reference"]["cost_cap"],total_cap=result["total_cap"],
                    participant_cap=result["cost_tiebreak_participant_cap"])
                check = verify(state,plan,**caps)
                require(check["valid"],"saved selected plan failed verification")
                for field in ("cost","d_vpp","d_edge","n_adjust"):
                    require(abs(check[field]-row[field])<TOL,"saved metric mismatch")
                max_residual = max(max_residual,max(check["residuals"].values()))
                selected_checks += 1
            if label!="economic":
                require(abs(row["epsilon"]-row["alpha"]*0.8*8)<1e-12,"budget changed")
            rows.append(row)
        for entry in record["solve_trace"]:
            traces.append(entry)
            result = entry.get("result")
            if result is None:
                all_optimal = False
                continue
            all_optimal &= result["optimal"]
            if result["plan"] is None:
                continue
            stage_state = case if entry["role"]=="nominal" else state
            caps = {}
            if entry["role"] not in ("nominal","event_economic"):
                detail = record["configurations"][entry["role"]]["detail"]
                caps["cost_cap"] = (detail["cost_tiebreak_effective_cap"] if entry["objective"]=="economic"
                                    else detail["economic_reference"]["cost_cap"])
                caps["participant_cap"] = (detail["cost_tiebreak_participant_cap"] if entry["objective"]=="economic"
                                           else detail["participant_cap"])
                if entry["objective"]!="total":
                    caps["total_cap"] = detail["total_cap"]
            check = verify(stage_state,result["plan"],**caps)
            require(check["valid"],"saved traced phase failed verification")
            max_residual = max(max_residual,max(check["residuals"].values()))
            stage_checks += 1
        configs = record["configurations"]
        economic = configs["economic"]["summary"]
        zero,low,high = [configs[k]["summary"] for k in ("alpha_0","alpha_0p1","alpha_0p3")]
        if record["family"]=="G4":
            # Constructing a saved witness invokes no optimizer and does not
            # extend the tested budget grid. It explains feasibility versus cost.
            constructed = standby_witness(state)
            pre_cost = verify(before,published)["cost"]
            window = range(state["event"]["task"]["arrival"],state["event"]["task"]["deadline"])
            reference_export = sum(published["grid"]["sell"][k]*state["dt"] for k in window)
            extra = constructed["validation"]["cost"]-economic["cost"]
            require(constructed["validation"]["valid"],"invalid standby preservation witness")
            expected_extra = (1.01-0.8)*8+(0.8-0.2)*reference_export
            require(abs(extra-expected_extra)<TOL,"standby purchase/sale cost decomposition mismatch")
            standby_diagnostics.append(dict(case_id=name,profile=record["profile"],
                original_event_export_kwh=reference_export,
                common_economic_cost_increment=economic["cost"]-pre_cost,
                constructed_full_preservation_extra_cost=extra,
                high_budget=high["epsilon"],high_budget_total_deviation=high["d_vpp"],
                full_preservation_witness=constructed))
        if all(r["valid"] for r in (zero,low,high)):
            for smaller,larger in ((zero,low),(low,high)):
                if smaller["total_optimal"] and larger["total_optimal"]:
                    require(larger["d_vpp"]<=smaller["d_vpp"]+TOL,"budget monotonicity violation")
                    monotonicity_checks += 1
            original = original_results[record["template"]]
            comparisons.append(dict(case_id=name,template=record["template"],family=record["family"],
                profile=record["profile"],economic_D=economic["d_vpp"],
                zero_D=zero["d_vpp"],low_D=low["d_vpp"],high_D=high["d_vpp"],
                zero_N=zero["n_adjust"],low_N=low["n_adjust"],high_N=high["n_adjust"],
                high_extra_cost=high["extra_cost"],
                zero_reduction=1-zero["d_vpp"]/economic["d_vpp"] if economic["d_vpp"]>TOL else None,
                original_zero_D=original["zero_D"],original_high_D=original["high_D"],
                original_high_N=original["high_N"],
                original_reference_event_net_import=sum((state["reference"]["grid"]["buy"][k]-
                    state["reference"]["grid"]["sell"][k])*state["dt"] for k in (48,49)),
                certificate_energy=cert["absorption_energy"]))
    require(rechecked_configs==96 and len(traces)==summary["actual_solver_calls"],"count mismatch")
    grouped = {}
    for profile in ("DAY_SURPLUS","CLOUD_EVENING"):
        grouped[profile] = {}
        for family in ("G1","G2","G3","G4"):
            cases = [r for r in comparisons if r["profile"]==profile and r["family"]==family]
            grouped[profile][family] = dict(cases=len(cases),
                zero_budget_zero_modification=sum(r["zero_D"]<TOL for r in cases),
                high_budget_zero_modification=sum(r["high_D"]<TOL for r in cases),
                high_budget_improves_total=sum(r["high_D"]<r["zero_D"]-TOL for r in cases))
    reductions = [r["zero_reduction"] for r in comparisons if r["zero_reduction"] is not None]
    timings = sorted(r["event_to_verified_wall_seconds"] for r in rows if r["method"]!="economic")
    audit = dict(cases=24,configurations=rechecked_configs,selected_plans_reverified=selected_checks,
        nominal_plans_reverified=nominal_checks,actual_solve_trace_count=len(traces),
        phase_plans_reverified=stage_checks,constructive_witnesses_reverified=witnesses,
        all_phases_optimal=bool(all_optimal),maximum_raw_constraint_residual=max_residual,
        frozen_inputs_and_execution_sources_match=True,first_batch_unchanged=True,
        curve_recipes_independently_checked=True,publications_shared_within_each_case=True,
        budget_monotonicity_checks=monotonicity_checks,
        zero_budget_improved_cases=sum(r["zero_D"]<r["economic_D"]-TOL for r in comparisons),
        zero_budget_reduction_range=[min(reductions),max(reductions)],groups=grouped,
        standby_preservation_witness_count=len(standby_diagnostics),
        measured_repair_wall_seconds=dict(median=timings[len(timings)//2],max=max(timings),
            note="Includes synchronous solve-trace persistence; protocol differs from stage1, not a speed comparison"),
        comparisons=comparisons,
        standby_diagnostics=[{k:v for k,v in d.items() if k!="full_preservation_witness"}
                             for d in standby_diagnostics])
    write(OUT/"standby_preservation_witnesses.json",standby_diagnostics)
    evidence = sorted(DATA.glob("*.json"))+sorted((OUT/"cases").glob("*.json"))+[
        OUT/"summary.json",OUT/"configurations.csv",OUT/"standby_preservation_witnesses.json",
        ROOT/"audit_main_stage2.py"]
    audit["evidence_sha256"] = {str(p.relative_to(ROOT)):sha(p) for p in evidence}
    write(OUT/"audit.json",audit)
    lines = ["# 主体实验第二批：已知运行曲线变化", "",
        f"24例、96配置完成；独立复核{selected_checks}个最终方案和{stage_checks}个求解阶段方案。",
        f"全部阶段最优：{all_optimal}；原第一批数据和结果保持不变。", "",
        "DAY_SURPLUS：10:00—16:00负荷乘0.9，全天光伏乘1.1，风电不变。",
        "CLOUD_EVENING：11:30—12:30光伏乘0.65，16:00—22:00负荷乘1.1，",
        "风电12:00前乘0.8、此后乘1.2。其他时间乘1；价格和设备参数不变。",
        "两条曲线均在日前已知，每例重新生成原计划；这不是预测误差实验。", "",
        "## 配对结果", "",
        "|模板|曲线|经济解总修改|零预算总修改|低预算总修改|高预算总修改|零/低/高参与数|高预算额外成本|",
        "|---|---|---:|---:|---:|---:|---|---:|"]
    for r in comparisons:
        lines.append(f"|{r['template']}|{r['profile']}|{r['economic_D']:.5f}|{r['zero_D']:.5f}|"
            f"{r['low_D']:.5f}|{r['high_D']:.5f}|{r['zero_N']}/{r['low_N']}/{r['high_N']}|"
            f"{r['high_extra_cost']:.5f}|")
    lo,hi = audit["zero_budget_reduction_range"]
    lines += ["", "## 解释口径", "",
        f"零预算择优在{audit['zero_budget_improved_cases']}/24例减少相对当次经济解的总修改，"
        f"幅度为{100*lo:.1f}%—{100*hi:.1f}%。这是经济带内择优，尚非外部方法比较。",
        "原曲线与两条新曲线的结果按模板配对保留在audit.json，不能只选有利曲线。",
        "DAY_SURPLUS下G3三例正预算均进一步降低总修改，CLOUD_EVENING下三例仍不敏感。",
        "这说明该类运行结果依赖曲线和相应原计划，不能仅依据G3标签将收益全部归因于EV窗口。",
        "G2远端模板在两组新曲线下均保持总计划；本地模板在午间云段下不再完全保持。", "",
        "## 备用资源组的预算边界", "",
        "DAY_SURPLUS时原计划在事件窗口存在外售电量，新增用电先减少外售，再增加外购。",
        "因此相比事件后最低成本方案，保持原发布计划不只是替代0.8元/kWh外购，还要保持原外售。",
        "在本组条件下，构造完全保持方案的额外成本可分解为0.21*8+0.6*原外售电量。",
        "三种分布的构造均通过独立核验，DAY_SURPLUS下需要约2.2094元，超过1.92元预算，",
        "故预算内最优总修改仍约0.357286kWh。该构造证明更大预算下存在完全保持方案，",
        "本轮没有为此追加预算扫描。CLOUD_EVENING下额外1.68元即可完全保持。", "",
        "不同曲线会改变发布计划、初始实际状态以及当时购售电方向，不能沿用原曲线的",
        "弃电余量、参与数或0.21元/kWh备用相对成本结论而不重新检查。",
        "记录同步保存各求解阶段完整轨迹；墙钟时间含这些保存开销，协议与第一批不同，",
        "不能直接用两批时间差评价算法速度。求解器自身时间另存。",
        "这是两条针对性确定性曲线，不作独立随机样本或概率可靠率推断。",
        "尚未运行文献对比、消融或新事件时刻实验。"]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k not in ("comparisons","evidence_sha256","standby_diagnostics")},
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
