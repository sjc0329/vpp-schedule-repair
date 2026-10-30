"""Postprocess existing results only; does not run an optimizer."""
import hashlib
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",default="results/basic")
    args = parser.parse_args()
    out = ROOT/args.output
    records = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted((out/"cases").glob("*.json"))]
    summary = json.loads((out/"summary.json").read_text(encoding="utf-8"))
    phases = [p for r in records for p in r["phases"].values()]
    validations = [p["validation"] for p in phases if "validation" in p]
    source_files = list((ROOT/"vpp_repair").glob("*.py")) + [
        ROOT/"run_validation.py",ROOT/"run_mechanism_validation.py",ROOT/"audit_results.py",
        ROOT/"requirements.txt",ROOT/"README.md",ROOT/"RESEARCH_SPEC.md"]
    audit = dict(
        meaning="Release-file fingerprints and read-only analysis of completed validation",
        all_passed=summary["all_passed"],
        case_count=len(records),
        actual_backend_audits_passed=sum(r["backend_audit"]["valid"] for r in records),
        solver_phase_count=len(phases),
        independently_validated_plan_count=len(validations),
        budget_frontier_checks=sum(k.startswith("budget_")
                                   for r in records for k in r["phases"]),
        participation_checks=sum(k.startswith("scope_")
                                 for r in records for k in r["phases"]),
        semantic_checks_passed=sum(summary["semantics"].values()),
        maximum_raw_constraint_residual=max(
            v for item in validations for v in item["residuals"].values()),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in source_files},
        dataset_sha256={str(p.relative_to(ROOT)):sha(p)
                        for p in sorted((ROOT/"datasets"/"basic").glob("*.json"))})
    (out/"audit.json").write_text(
        json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    report = out/"REPORT.md"
    text = report.read_text(encoding="utf-8")
    text = text.split("\n## 汇总审核\n")[0]
    text += ("\n## 汇总审核\n\n"
             f"- 实际后端矩阵审核：{audit['actual_backend_audits_passed']}/{len(records)}\n"
             f"- 预算曲线检查：{audit['budget_frontier_checks']}\n"
             f"- 调整范围检查：{audit['participation_checks']}\n"
             f"- 语义及错误注入检查：{audit['semantic_checks_passed']}\n"
             f"- 独立核验的可行计划：{audit['independently_validated_plan_count']}\n"
             f"- 最大原始约束残差：{audit['maximum_raw_constraint_residual']:.3g}\n\n"
             "audit.json 保存当前交付源文件和数据指纹。"
             "这些是正确性检查计数，不是统计独立样本数或性能优势。\n")
    report.write_text(text,encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if not k.endswith("sha256")},
                     ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
