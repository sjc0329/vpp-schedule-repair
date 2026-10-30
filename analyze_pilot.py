"""Re-audit saved pilot evidence and explain results; never calls a solver."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform

from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/"results"/"pilot_v1_corrected"
DATA = ROOT/"datasets"/"pilot_v1_corrected"
TOL = 2e-6


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def energy(state,plan):
    start,dt = state["prefix"],state["dt"]
    n = state["horizon"]
    losses = sum(((1-r["eta_c"])*plan["resources"][r["id"]]["charge"][k]
                  +(1/r["eta_d"]-1)*plan["resources"][r["id"]]["discharge"][k])*dt
                 for r in state["resources"] for k in range(start,n))
    return dict(net_import=sum((plan["grid"]["buy"][k]-plan["grid"]["sell"][k])*dt
                               for k in range(start,n)),
                renewable=sum(plan["edges"][e]["renewable"][k]*dt
                              for e in state["load"] for k in range(start,n)),
                terminal_energy=sum(plan["resources"][r["id"]]["energy"][n]
                                    for r in state["resources"]),losses=losses)


def main():
    records = [read(p) for p in sorted((OUT/"cases").glob("*.json"))]
    checks,comparisons,stage_examples,budget_examples = [],[],[],[]
    identity_residual = 0.0
    source_manifest = read(DATA/"manifest.json")
    unchanged = all(sha(ROOT/p) == digest
                    for p,digest in source_manifest["source_sha256"].items())
    assert unchanged,"pilot implementation changed after its source manifest"
    original = read(ROOT/"datasets"/"pilot_v1"/"manifest.json")
    assert original["input_sha256"] == source_manifest["input_sha256"],"input tuning occurred"
    for record in records:
        name = record["case_id"]
        state = read(DATA/(name+"-event-state.json"))
        full = read(DATA/(name+"-input.json"))
        assert verify(full,record["nominal"]["plan"])["valid"]
        configs = record["configurations"]
        baseline = configs["economic"]["summary"]
        zero = configs["zero_budget"]["summary"]
        budget = configs["budget_1.92"]["summary"]
        best = min((v["summary"] for v in configs.values() if v["summary"]["valid"]),
                   key=lambda row:row["d_vpp"])
        comparisons.append(dict(case_id=name,economic_d=baseline["d_vpp"],zero_d=zero["d_vpp"],
            reduction_fraction=1-zero["d_vpp"]/baseline["d_vpp"],
            budget_improvement=zero["d_vpp"]-budget["d_vpp"],
            best_d=best["d_vpp"]))
        reference_energy = energy(state,state["reference"])
        for label,item in configs.items():
            detail = item["detail"]
            plan = detail["plan"] if label == "economic" else detail["selected_plan"]
            if plan is None:
                continue
            caps = {} if label == "economic" else dict(
                cost_cap=detail["economic_reference"]["cost_cap"],
                participant_cap=detail["participant_cap"],total_cap=detail["total_cap"])
            check = verify(state,plan,**caps)
            assert check["valid"],(name,label,check["errors"])
            for field in ("cost","d_vpp","d_edge","n_adjust"):
                assert abs(check[field]-item["summary"][field]) < TOL
            e = energy(state,plan)
            delta_import = e["net_import"]-reference_energy["net_import"]
            decomposition = (8-(e["renewable"]-reference_energy["renewable"])
                             +e["terminal_energy"]-reference_energy["terminal_energy"]
                             +e["losses"]-reference_energy["losses"])
            identity_residual = max(identity_residual,abs(delta_import-decomposition))
            assert abs(delta_import-decomposition) < TOL
            assert check["d_vpp"]+TOL >= abs(delta_import)
            checks.append(dict(case_id=name,method=label,valid=True,
                               delta_import=delta_import,energy_decomposition=decomposition))
            if label != "economic":
                total = detail["phases"]["total"]["validation"]
                if (abs(total["d_vpp"]-check["d_vpp"]) < TOL
                        and total["n_adjust"] == check["n_adjust"]
                        and total["cost"]+TOL < check["cost"]):
                    stage_examples.append(dict(case_id=name,method=label,
                        total_stage_cost=total["cost"],selected_cost=check["cost"],
                        excess=check["cost"]-total["cost"]))
                witness = configs["zero_budget"]["summary"]
                if (abs(witness["d_vpp"]-check["d_vpp"]) < TOL
                        and witness["n_adjust"] == check["n_adjust"]
                        and witness["cost"]+TOL < check["cost"]
                        and witness["n_adjust"] <= detail["participant_cap"]
                        and witness["d_vpp"] <= detail["total_cap"]+2e-7):
                    budget_examples.append(dict(case_id=name,method=label,
                        witness="zero_budget",witness_cost=witness["cost"],
                        selected_cost=check["cost"],excess=check["cost"]-witness["cost"]))
    # Explicitly identify duplicate physical cases instead of treating them as
    # separate independent evidence merely because their labels differ.
    physical = {}
    duplicates = []
    for p in sorted(DATA.glob("*-input.json")):
        case = read(p)
        name = case["id"]
        for field in ("id","family","level","reference"):
            case.pop(field,None)
        case["event"].pop("id",None)
        digest = hashlib.sha256(json.dumps(case,sort_keys=True).encode()).hexdigest()
        if digest in physical:
            duplicates.append([physical[digest],name])
        else:
            physical[digest] = name
    old_audit = read(ROOT/"results"/"basic"/"audit.json")
    assert all(sha(ROOT/p) == digest for p,digest in old_audit["dataset_sha256"].items())
    audit = dict(saved_configurations_reverified=len(checks),source_manifest_matches=True,
        runtime=dict(python=platform.python_version(),
                     highspy=importlib.metadata.version("highspy")),
        input_matches_failed_attempt=True,original_36_case_dataset_unchanged=True,
        unique_physical_inputs=len(physical),duplicate_case_pairs=duplicates,
        zero_budget_improved_cases=sum(c["reduction_fraction"] > TOL for c in comparisons),
        additional_budget_improved_cases=sum(c["budget_improvement"] > TOL for c in comparisons),
        zero_budget_reduction_range=[min(c["reduction_fraction"] for c in comparisons),
                                     max(c["reduction_fraction"] for c in comparisons)],
        energy_identity_max_residual=identity_residual,
        same_primary_metrics_but_costlier_final_selections=stage_examples,
        same_primary_metrics_but_costlier_than_zero_budget=budget_examples,
        all_nominal_economic_references_optimal=all(r["nominal"]["optimal"] for r in records),
        all_post_event_economic_references_optimal=all(
            r["common_economic_reference"]["optimal"] for r in records),
        all_total_and_coordination_stages_optimal=all(
            item["detail"]["total_proven_optimal"] and item["detail"]["coordination_proven_optimal"]
            for r in records for label,item in r["configurations"].items() if label != "economic"),
        comparisons=comparisons,energy_checks=checks)
    evidence = (sorted((OUT/"cases").glob("*.json"))+sorted(DATA.glob("*.json"))
                +[OUT/"summary.json",OUT/"configurations.csv"])
    audit["evidence_sha256"] = {str(p.relative_to(ROOT)):sha(p) for p in evidence}
    (OUT/"analysis.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",
                                      encoding="utf-8")
    lines = ["# 先导结果解释与审核", "",
        "42个配置已从保存的完整轨迹重新独立核验，全部可行。",
        "原36例数据未变；修复前后九个输入文件指纹一致，未作结果导向调参。",
        f"九个标签对应{len(physical)}个不同物理输入；重复条件：{duplicates}。", "",
        "## 主要结果", "",
        "|案例|经济调度总修改/kWh|零预算总修改/kWh|1.92元预算再减少/kWh|",
        "|---|---:|---:|---:|"]
    for c in comparisons:
        lines.append(f"|{c['case_id']}|{c['economic_d']:.4f}|{c['zero_d']:.4f}|"
                     f"{max(0,c['budget_improvement']):.4f}|")
    low,high = audit["zero_budget_reduction_range"]
    lines += ["",f"零预算择优在9个标签案例中均减少总修改，幅度为{100*low:.1f}%—{100*high:.1f}%。",
        "这是相对于本次求解器返回的经济方案；收益主要反映经济近等价解的计划保持择优，",
        "不能据此归因于求解加速或宣称统计推广。1.92元预算只在3个标签案例中进一步减少总修改。", "",
        "## 协调作用与尚未闭合的择优", "",
        "ECON-3在总修改均为8kWh时，零预算、0.64元预算、1.92元预算的参与数分别为2、1、0。",
        "TIME-2在0.64元预算下，K=1时总修改约7.5125kWh，K=2时约7.3814kWh。",
        "SCOPE组三例均可由事件Edge及外购完成相应修复，未激活预期的跨Edge支援瓶颈；",
        "故本轮没有建立资源分布影响的有力证据。", "",
        "最终协调阶段只最小化参与数，未继续最小化成本；同等总修改和参与数下，",
        f"有{len(budget_examples)}个正预算配置存在已保存的零预算方案作为更低成本证据，",
        "该证据方案满足对应预算和范围上限，且总计划修改及参与数相同（在审核容差内）。",
        "因此不能把所报告最终成本解释为实现该计划保持及参与范围所需的最低代价。",
        "未在本轮追加成本择优阶段，也未修改这些结果。", "",
        "## 为什么增加预算通常难以消除8kWh修改", "",
        "总能量平衡给出：新增净购电 = 新任务电量 - 新增风光利用 + 终端储能变化 + 损耗变化。",
        "初始状态和固定负荷相同，未来无新增接入能量差异时，该式对各方法成立。",
        "逐时段绝对修改量至少不小于净购电增量的绝对值；本轮已逐配置审核这两个关系。",
        "因此在原计划已充分利用风光、终端电量接近硬要求的场景，8kWh新增需求大部分必须",
        "通过额外购电满足，内部重新安排主要改变损耗和时序，不能凭经济预算凭空消除能量需求。",
        "这是对本轮有限收益的结构解释，具体贡献仍须结合逐例电量分解判断。", "",
        "## 执行记录", "",
        "results/pilot_v1保存首次尝试的错误记录：导出时丢失非零历史前缀，被独立核验器拒绝。",
        "修复导出后以相同输入运行pilot_v1_corrected；这不是因效果不好而调整数据。",
        "另以36例原解析回归和8组机制检查核验修复，包含非零前缀、剩余任务与EV接入。",
        "未实施连续事件、新事件强度或额外参数搜索。"]
    (OUT/"ANALYSIS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k not in
        ("comparisons","energy_checks","same_primary_metrics_but_costlier_final_selections",
         "same_primary_metrics_but_costlier_than_zero_budget","evidence_sha256")},
        ensure_ascii=False,indent=2))
    print("Costlier equivalent-primary final selections:",len(stage_examples))
    print("Costlier than feasible zero-budget witnesses:",len(budget_examples))


if __name__ == "__main__":
    main()
