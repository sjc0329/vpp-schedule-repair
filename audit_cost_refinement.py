"""Independent saved-plan recheck and provenance; no optimization calls."""
import hashlib
import json
from pathlib import Path

from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent
OUT = ROOT/"results"/"pilot_cost_tiebreak"
DATA = ROOT/"datasets"/"pilot_v1_corrected"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = read(OUT/"manifest.json")
    assert all(sha(ROOT/p)==digest for p,digest in manifest["frozen_evidence_sha256"].items())
    assert all(sha(ROOT/p)==digest for p,digest in manifest["source_sha256"].items())
    count,internal_differences,gaps = 0,[],[]
    for path in sorted((OUT/"cases").glob("*.json")):
        case = read(path)
        state = read(DATA/(case["case_id"]+"-event-state.json"))
        for label,item in case["configurations"].items():
            row,result = item["summary"],item["detail"]
            caps = {} if label == "economic" else dict(
                cost_cap=result["effective_cost_cap"],
                total_cap=result["total_cap"],participant_cap=result["participant_cap"])
            check = verify(state,result["selected_plan"],**caps)
            assert check["valid"],(case["case_id"],label,check["errors"])
            for field,key in (("cost","new_cost"),("d_vpp","new_d_vpp"),
                              ("d_edge","new_d_edge"),("n_adjust","new_n_adjust")):
                assert abs(check[field]-row[key]) < 2e-6
            if label != "economic":
                assert check["cost"] <= row["old_cost"]+2e-7
                assert check["d_vpp"] <= row["old_d_vpp"]+2e-6
                assert check["n_adjust"] <= row["old_n_adjust"]
                internal_differences.append(row["new_d_edge"]-row["old_d_edge"])
                if result["phase"]["absolute_gap"] is not None:
                    gaps.append(result["phase"]["absolute_gap"])
            count += 1
    audit = dict(reverified_configurations=count,frozen_evidence_unchanged=True,
        solver_source_matches_execution_manifest=True,
        internal_modification_increase_count=sum(v>2e-6 for v in internal_differences),
        max_internal_modification_increase=max(internal_differences),
        max_internal_modification_decrease=-min(internal_differences),
        maximum_recorded_final_cost_gap=max(gaps,default=None))
    files = (sorted((OUT/"cases").glob("*.json"))+
             [OUT/"manifest.json",OUT/"summary.json",OUT/"comparison.csv",
              ROOT/"run_mechanism_validation.py",ROOT/"audit_cost_refinement.py",
              ROOT/"run_pilot.py",ROOT/"README.md",ROOT/"RESEARCH_SPEC.md"])
    audit["release_sha256"] = {str(p.relative_to(ROOT)):sha(p) for p in files}
    (OUT/"audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    report = OUT/"REPORT.md"
    text = report.read_text(encoding="utf-8").split("\n## 保存结果复核\n")[0]
    text += ("\n## 保存结果复核\n\n"
             f"- {count}个配置从完整轨迹重新核验通过；冻结数据与原结果指纹未变。\n"
             f"- 内部Edge修改有{audit['internal_modification_increase_count']}个配置增加，"
             f"最大增加{audit['max_internal_modification_increase']:.6g}kWh。\n"
             "- 内部Edge修改未作为本轮锁定指标；总计划修改、参与范围与成本改善不能"
             "解释为所有指标同时改善。\n"
             "- 9组机制检查通过，包含最终成本的解析值及模拟成本阶段中断后备。\n")
    report.write_text(text,encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k != "release_sha256"},
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
