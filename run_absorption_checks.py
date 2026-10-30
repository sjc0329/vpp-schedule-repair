"""Analytical tests of the direct-absorption certificate, without optimization."""
from copy import deepcopy
from pathlib import Path

from run_pilot import require,write
from vpp_repair.absorption import certify_absorption
from vpp_repair.data import base,make_reference,task,apply_event,resource
from vpp_repair.verify import verify

ROOT = Path(__file__).resolve().parent


def fixture(name,energy=1,deadline=5):
    case = base(name)
    case["group"] = "CHECK"
    case["event"] = dict(id=name+"-event",type="new_task",edge="A",
        version_from=0,version_to=1,task=task("new","A",energy,4,deadline,4))
    return case


def state(case):
    if "reference" not in case:
        case["reference"] = make_reference(case)
    return apply_event(case,case["event"])[0]


def main():
    records = {}
    local = fixture("CERT-LOCAL")
    local["pv"]["A"][4] = 4
    cert = certify_absorption(state(local))
    require(cert["direct_absorption_sufficient"],"local certificate missed")
    require(cert["minimum_direct_support_edges"] == 0,"unneeded local coordination")
    require(cert["witness_validation"]["d_edge"] < 1e-9,"local interface changed")
    records["local_absorption"] = cert

    remote = fixture("CERT-REMOTE")
    remote["pv"]["C"][4] = 4
    cert = certify_absorption(state(remote))
    require(cert["minimum_direct_support_edges"] == 1,"remote count wrong")
    require(abs(cert["witness_validation"]["d_edge"]-2) < 1e-9,"remote compensation wrong")
    records["remote_absorption"] = cert

    limited = fixture("CERT-SOURCE-LIMIT")
    limited["pv"]["C"][4] = 44
    limited["load"]["B"][4] = 40
    limited["reference"] = make_reference(limited)
    limited["reference"]["edges"]["C"]["renewable"][4] = 40
    limited["reference"]["edges"]["C"]["q"][4] = 40
    limited["reference"]["grid"]["buy"][4] = 0
    limited["reference"]["grid"]["mode"][4] = 0
    cert = certify_absorption(state(limited))
    require(not cert["direct_absorption_sufficient"],"ignored source interface limit")
    records["source_interface_limit"] = cert

    limited = fixture("CERT-SINK-LIMIT")
    limited["load"]["A"][4] = 40
    limited["pv"]["C"][4] = 4
    limited["import_limit"][4] = 40
    cert = certify_absorption(state(limited))
    require(not cert["direct_absorption_sufficient"],"ignored sink interface limit")
    records["sink_interface_limit"] = cert

    window = fixture("CERT-WINDOW",energy=1.25,deadline=6)
    window["pv"]["A"][4] = 8
    window["pv"]["A"][6] = 100
    cert = certify_absorption(state(window))
    require(abs(cert["absorption_energy"]-1) < 1e-9,"ignored task power/window")
    require(not cert["direct_absorption_sufficient"],"used surplus outside task window")
    records["task_power_and_deadline"] = cert

    multi = fixture("CERT-MULTI")
    multi["pv"]["B"][4] = multi["pv"]["C"][4] = 2
    cert = certify_absorption(state(multi))
    require(cert["minimum_direct_support_edges"] == 2,"subset certificate wrong")
    records["minimum_direct_support_subset"] = cert

    # No renewable certificate, yet a valid storage-based repair exists.
    flexible = fixture("CERT-NOT-NECESSARY")
    flexible["resources"] = [resource("a_store","A",60,discharge={4:4})]
    updated = state(flexible)
    cert = certify_absorption(updated)
    require(not cert["direct_absorption_sufficient"],"unexpected renewable certificate")
    witness = deepcopy(updated["reference"])
    witness["version"] = updated["version"]
    witness["tasks"]["new"] = [0.0]*updated["horizon"]
    witness["tasks"]["new"][4] = 4
    witness["resources"]["a_store"]["discharge"][4] = 4
    for k in range(5,updated["horizon"]+1):
        witness["resources"]["a_store"]["energy"][k] = 60-1/0.9
    validation = verify(updated,witness,total_cap=0)
    require(validation["valid"],"storage counterexample invalid")
    records["certificate_is_sufficient_not_necessary"] = dict(
        certificate=cert,storage_witness=witness,validation=validation)
    write(ROOT/"results"/"absorption_checks"/"checks.json",
          dict(all_passed=True,count=len(records),checks=records))
    print("Absorption certificate analytical checks: 7/7 PASS")


if __name__ == "__main__":
    main()
