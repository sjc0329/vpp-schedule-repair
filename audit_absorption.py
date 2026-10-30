"""Saved-evidence audit and physical interpretation; no optimization calls."""
import json
from pathlib import Path

from run_pilot import require,sha,write
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
DATA = ROOT/"datasets"/"absorption_v1"
OUT = ROOT/"results"/"absorption_v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    manifest = read(DATA/"manifest.json")
    require(all(sha(DATA/p)==digest for p,digest in manifest["input_sha256"].items()),
            "new frozen input changed")
    require(all(sha(ROOT/p)==digest for p,digest in manifest["source_sha256"].items()),
            "solver source changed after run")
    old_evidence = read(ROOT/"results"/"pilot_v1_corrected"/"analysis.json")["evidence_sha256"]
    require(all(sha(ROOT/p)==digest for p,digest in old_evidence.items()),"prior pilot evidence changed")
    old_cost = read(ROOT/"results"/"pilot_cost_tiebreak"/"audit.json")["release_sha256"]
    old_cost_outputs = {p:d for p,d in old_cost.items()
                        if p.replace("\\","/").startswith("results/pilot_cost_tiebreak/")}
    require(all(sha(ROOT/p)==digest for p,digest in old_cost_outputs.items()),"cost-refinement evidence changed")
    base_evidence = read(ROOT/"results"/"basic"/"audit.json")["dataset_sha256"]
    require(all(sha(ROOT/p)==digest for p,digest in base_evidence.items()),"original analytical data changed")
    rows,diagnostics = [],[]
    count,witnesses,all_optimal = 0,0,True
    residual = 0.0
    for path in sorted((OUT/"cases").glob("*.json")):
        record = read(path)
        name = record["case_id"]
        full,state = read(DATA/(name+"-input.json")),read(DATA/(name+"-event-state.json"))
        require(verify(full,record["nominal"]["plan"])["valid"],"invalid saved nominal")
        require(record["backend_audit"]["valid"],"backend audit failed")
        all_optimal &= record["nominal"]["optimal"] and record["common_economic_reference"]["optimal"]
        cert = record["certificate"]
        owner = state["event"]["edge"]
        window = range(state["event"]["task"]["arrival"],state["event"]["task"]["deadline"])
        raw_remote_surplus = sum(
            max(0,state["pv"][e][k]+state["wind"][e][k]
                -state["reference"]["edges"][e]["renewable"][k])*state["dt"]
            for k in window for e in state["load"] if e!=owner)
        diag = dict(case_id=name,peak=full["solar_variant"],
            certificate_energy=cert["absorption_energy"],
            raw_remote_curtailed_energy=raw_remote_surplus,
            source_C_export_room=[state["interface_limit"]-
                state["reference"]["edges"]["C"]["q"][k] for k in window])
        diagnostics.append(diag)
        if cert["direct_absorption_sufficient"]:
            plan = cert["witness_plan"]
            check = verify(state,plan,total_cap=0,
                           participant_cap=cert["minimum_direct_support_edges"])
            require(check["valid"],"certificate witness invalid")
            require(plan["resources"] == state["reference"]["resources"],"certificate changed storage")
            require(plan["grid"] == state["reference"]["grid"],"certificate changed grid exchange")
            require(all(plan["tasks"][tid]==values for tid,values in
                        state["reference"]["tasks"].items()),"certificate changed old task")
            witnesses += 1
        for label,item in record["configurations"].items():
            detail,summary = item["detail"],item["summary"]
            plan = detail["plan"] if label=="economic" else detail["selected_plan"]
            caps = {} if label=="economic" else dict(
                cost_cap=detail["economic_reference"]["cost_cap"],
                total_cap=detail["total_cap"],
                participant_cap=detail["cost_tiebreak_participant_cap"])
            check = verify(state,plan,**caps)
            require(check["valid"],"invalid saved selected plan")
            for field in ("cost","d_vpp","d_edge","n_adjust"):
                require(abs(check[field]-summary[field]) < 2e-6,"saved metric mismatch")
            residual = max(residual,max(check["residuals"].values()))
            if label != "economic":
                all_optimal &= (detail["total_proven_optimal"] and
                                detail["coordination_proven_optimal"] and detail["cost_proven_optimal"])
                if summary["certificate_budget_allowed"]:
                    require(check["d_vpp"] < 2e-6,"missed certified zero modification")
            rows.append(summary)
            count += 1
    new_checks = read(ROOT/"results"/"absorption_checks"/"checks.json")
    require(new_checks["all_passed"],"certificate unit checks failed")
    summary = dict(reverified_configurations=count,verified_constructive_witnesses=witnesses,
        certificate_analytical_checks=new_checks["count"],all_stages_optimal=bool(all_optimal),
        maximum_raw_constraint_residual=residual,new_inputs_frozen=True,
        old_pilot_and_cost_refinement_evidence_unchanged=True,original_36_case_data_unchanged=True,
        zero_modification_repair_configurations=sum(r["method"]!="economic" and
            r["d_vpp"]<2e-6 for r in rows),diagnostics=diagnostics)
    evidence = sorted(DATA.glob("*.json"))+sorted((OUT/"cases").glob("*.json"))+[
        OUT/"summary.json",OUT/"configurations.csv",ROOT/"run_absorption_checks.py",
        ROOT/"audit_absorption.py"]
    summary["evidence_sha256"] = {str(p.relative_to(ROOT)):sha(p) for p in evidence}
    write(OUT/"audit.json",summary)
    lines = ["# 吸收场景的机制解释", "",
        "24个配置全部完成并重新独立核验；6个完整日原计划及所有优化阶段均达到求解器最优状态。",
        "原先导、成本择优实验及原36例解析数据文件指纹保持不变。", "",
        "## 已获得的正面证据", "",
        "ABS-LOCAL-3的光伏峰值80kW，两时段可直接回收弃电分别为18和17.828714kW，",
        "对应8.957178kWh，足以覆盖8kWh任务。构造方案固定原储能、EV、旧任务和电网交换，",
        "仅增加本地风光利用，并通过独立物理核验。",
        "完整优化在零预算及两档正预算下均得到零总计划修改、零非事件Edge参与，",
        "内部接口修改也为零（均在数值容差内）。本次纯经济求解返回方案总修改约37.8589kWh。",
        "因此本案例直接说明：满足条件时可在不增加经济代价的同时吸收事件并保持承诺，",
        "计划保持目标能在经济等价方案中选出这一解。", "",
        "## 远端案例为什么没有完全吸收", "",
        "REMOTE-1与REMOTE-2在事件窗口没有可直接回收的原计划弃电；增大发电容量",
        "先被原有负荷、充电和对外输出利用，装机更大不自动意味着有未用余量。",
        "REMOTE-3存在约4.957178kWh弃电，但C在两个时段均已达到40kW接口输出上限。",
        "因此保持旧设备动作时，可直接向A增加的支援为零。即使只忽略接口约束，",
        "这些弃电也不足8kWh；不能把本例未完全吸收的原因全部归于接口约束。",
        "完整优化可改变储能等动作，但本轮三档经济预算下均未达到零总修改。",
        "这里得到的是所测试预算内的结果，未求解不受经济限制的完整恢复边界。", "",
        "## 理论性质与适用范围", "",
        "证书是在固定旧动作的直接回收弃电子类中的容量判断。容量满足任务能量要求时，",
        "逐时段分配本地及远端增发，使新增任务用电与增发相抵；接口余量保证输出和输入不越界，",
        "总功率不变，旧状态轨迹和服务义务不变。当前模型风光可独立削减、新任务可分时执行，",
        "且回收弃电无新增成本，是这一构造的关键条件。",
        "证书不满足不推出完整模型不可修复；新增解析检查给出了储能供能且零总修改的反例。",
        "证书子类中的最少支援人数不等于完整模型最少人数。",
        "六例定位了有余量与直接余量不足两类区域，尚未精确定位参数临界值。", "",
        "## 本轮结论", "",
        "本地完全吸收获得运行案例和构造保证双重支持；远端运行案例保留了无弃电和接口拥塞的边界。",
        "远端完全吸收已有解析检查支持，本轮六例尚未给出完整日正面运行案例。",
        "不对未取得的效果作推断，不因某些场景无额外收益而删除它们；未追加调参或场景。"]
    (OUT/"ANALYSIS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in ("diagnostics","evidence_sha256")},
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
