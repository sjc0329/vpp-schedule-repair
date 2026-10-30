"""Check accepted source/env provenance and frozen evidence without solving."""
import importlib.metadata
import json
from pathlib import Path
import sys

from run_pilot import require,sha,write

ROOT = Path(__file__).resolve().parent
OUT = ROOT/"results"/"baseline_acceptance_v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    accepted = read(OUT/"checks.json")
    require(accepted["summary"]["all_passed"],"acceptance did not pass")
    require(all(sha(ROOT/p)==digest for p,digest in accepted["summary"]["source_sha256"].items()),
            "accepted adapter/test source changed")
    preserved = {}
    for folder in ("main_stage1_v1","main_stage2_v1"):
        previous = read(ROOT/"results"/folder/"audit.json")["evidence_sha256"]
        physical_files = {p:d for p,d in previous.items()
                          if p.replace("\\","/").startswith(("datasets/","results/"))}
        require(all(sha(ROOT/p)==d for p,d in physical_files.items()),"frozen evaluation evidence changed")
        preserved[folder] = len(physical_files)
    core = read(ROOT/"datasets/main_stage2_v1/manifest.json")["source_sha256"]
    core = {k.replace("\\","/"):v for k,v in core.items()}
    require(all(sha(ROOT/name)==core[name] for name in
        ("vpp_repair/model.py","vpp_repair/repair.py","vpp_repair/verify.py","vpp_repair/data.py")),
        "core method changed")
    require(str(Path(sys.prefix)).lower()==str(ROOT/".venv-baselines").lower(),"wrong baseline environment")
    require(importlib.metadata.version("pyscipopt")==accepted["summary"]["runtime"]["pyscipopt"],
            "PySCIPOpt version differs")
    files = [ROOT/"BASELINE_ADAPTATION.md",ROOT/"requirements-baselines.txt",
             ROOT/"audit_baseline_acceptance.py",OUT/"checks.json",OUT/"REPORT.md"]
    audit = dict(all_passed=True,acceptance_groups=accepted["summary"]["check_groups"],
        original_core_unchanged=True,frozen_evaluation_evidence_unchanged=True,
        preserved_files=preserved,baseline_environment=str(sys.prefix),
        parameter_calibration_executed=False,formal_comparison_executed=False,
        release_sha256={str(p.relative_to(ROOT)):sha(p) for p in files})
    write(OUT/"audit.json",audit)
    print(json.dumps({k:v for k,v in audit.items() if k!="release_sha256"},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
