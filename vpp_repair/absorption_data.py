"""Six predeclared solar-capacity variants; no result-dependent selection."""
import math

from .pilot_data import build_case,empty_reference


def all_absorption_cases():
    cases = []
    for family,owner,peaks in (("LOCAL","A",(20,50,80)),
                               ("REMOTE","C",(40,60,80))):
        for level,peak in enumerate(peaks):
            case = build_case("TIME",2)
            case["id"] = f"ABS-{family}-{level+1}"
            case["family"],case["level"] = family,level+1
            case["solar_variant"] = dict(edge=owner,peak_kw=peak)
            case["pv"][owner] = [peak*math.sin(math.pi*(k*case["dt"]-6)/12)
                                  if 6 <= k*case["dt"] < 18 else 0.0
                                  for k in range(case["horizon"])]
            case["event"]["id"] = case["id"]+"-event"
            case["reference"] = empty_reference(case)
            cases.append(case)
    return cases
