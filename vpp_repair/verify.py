"""Independent checker: uses only raw input and plan, never the solver/matrix."""
import math

P_TOL, E_TOL, C_TOL, INT_TOL = 2e-7, 2e-7, 2e-7, 1e-7


def verify(s, plan, cost_cap=None, participant_cap=None, total_cap=None):
    n, start, dt = s["horizon"], s["prefix"], s["dt"]
    errors, residuals = [], {}

    def fail(label, violation, tol):
        violation = max(0.0, float(violation))
        residuals[label] = max(residuals.get(label, 0.0), violation)
        if violation > tol:
            errors.append(f"{label}: {violation:.12g} > {tol:.3g}")

    def arr(values, length, label):
        if len(values) != length or any(
            x is None or not math.isfinite(float(x)) for x in values):
            raise ValueError("malformed array: "+label)

    if plan["case_id"] != s["id"] or plan["version"] != s["version"]:
        errors.append("case/version mismatch")
    ref, cost, dq = s["reference"], 0.0, 0.0
    changed_q, changed_devices = set(), set()
    try:
        for e in s["load"]:
            for key in ("q", "renewable"):
                arr(plan["edges"][e][key], n, e+"."+key)
            for k in range(n):
                pe = plan["edges"][e]
                fail("interface", abs(pe["q"][k])-s["interface_limit"], P_TOL)
                available = s["pv"][e][k]+s["wind"][e][k]
                fail("renewable", max(-pe["renewable"][k],
                                     pe["renewable"][k]-available), P_TOL)
                if k < start:
                    fail("executed_prefix", abs(pe["q"][k]-ref["edges"][e]["q"][k]),
                         P_TOL)
                    fail("executed_prefix", abs(pe["renewable"][k]-
                         ref["edges"][e]["renewable"][k]), P_TOL)
                else:
                    diff = abs(pe["q"][k]-ref["edges"][e]["q"][k])
                    dq += diff*dt
                    if diff > P_TOL:
                        changed_q.add(e)
                    if abs(pe["renewable"][k]-
                           ref["edges"][e]["renewable"][k]) > P_TOL:
                        changed_devices.add(e)
        for r in s["resources"]:
            rid, e = r["id"], r["edge"]
            a, old = plan["resources"][rid], ref["resources"][rid]
            for key in ("charge", "discharge", "mode"):
                arr(a[key], n, rid+"."+key)
            arr(a["energy"][start:], n+1-start, rid+".energy")
            fail("initial_energy", abs(a["energy"][start]-r["initial_energy"]),
                 E_TOL)
            for k in range(start, n+1):
                lower = max(r["lower"][k], r["requirements"].get(str(k), 0))
                fail("energy_bounds", max(lower-a["energy"][k],
                                         a["energy"][k]-r["capacity"]), E_TOL)
            for k in range(n):
                c, d, z = a["charge"][k], a["discharge"][k], a["mode"][k]
                fail("battery_integrality", abs(z-round(z)), INT_TOL)
                fail("battery_mode_range", max(-z, z-1), INT_TOL)
                active = r.get("arrival",0) <= k < r["departure"]
                cb, db = (r["charge_limit"][k], r["discharge_limit"][k]) if active else (0,0)
                fail("charge_bound", max(-c, c-cb), P_TOL)
                fail("discharge_bound", max(-d, d-db), P_TOL)
                fail("charge_mode", c-r["charge_nameplate"]*z, P_TOL)
                fail("discharge_mode", d-r["discharge_nameplate"]*(1-z), P_TOL)
                fail("simultaneous_charge_discharge", min(c,d), P_TOL)
                if k < start:
                    fail("executed_prefix", max(abs(c-old["charge"][k]),
                                               abs(d-old["discharge"][k])), P_TOL)
                else:
                    incoming = (r.get("arrival_energy",0.0)
                                if k+1 == r.get("arrival",0) else 0.0)
                    predicted = (a["energy"][k] + r["eta_c"]*c*dt
                                 -d*dt/r["eta_d"]+incoming)
                    fail("energy_transition", abs(a["energy"][k+1]-predicted), E_TOL)
                    cost += r["wear"]*(c+d)*dt
                    if max(abs(c-old["charge"][k]),
                           abs(d-old["discharge"][k])) > P_TOL:
                        changed_devices.add(e)
        for t in s["tasks"]:
            tid, e = t["id"], t["edge"]
            u = plan["tasks"][tid]
            arr(u, n, tid)
            for k in range(n):
                bound = t["pmax"] if t["arrival"] <= k < t["deadline"] else 0
                fail("task_power_window", max(-u[k], u[k]-bound), P_TOL)
                old = ref["tasks"].get(tid, [0.0]*n)[k]
                if k < start:
                    fail("executed_prefix", abs(u[k]-old), P_TOL)
                elif abs(u[k]-old) > P_TOL:
                    changed_devices.add(e)
            fail("task_energy", abs(sum(u[start:])*dt-t["energy"]), E_TOL)
        for e in s["load"]:
            for k in range(start, n):
                supplied = plan["edges"][e]["renewable"][k]
                supplied += sum(plan["resources"][r["id"]]["discharge"][k]-
                                plan["resources"][r["id"]]["charge"][k]
                                for r in s["resources"] if r["edge"] == e)
                supplied -= s["load"][e][k]
                supplied -= sum(plan["tasks"][t["id"]][k]
                                for t in s["tasks"] if t["edge"] == e)
                fail("edge_balance", abs(plan["edges"][e]["q"][k]-supplied), P_TOL)
        grid = plan["grid"]
        for key in ("buy","sell","mode"):
            arr(grid[key], n, "grid."+key)
        for k in range(n):
            b, sell, z = grid["buy"][k], grid["sell"][k], grid["mode"][k]
            fail("grid_integrality", abs(z-round(z)), INT_TOL)
            fail("grid_mode_range", max(-z,z-1), INT_TOL)
            fail("grid_import", max(-b,b-s["import_limit"][k]), P_TOL)
            fail("grid_export", max(-sell,sell-s["export_limit"][k]), P_TOL)
            fail("grid_buy_mode", b-max(s["import_limit"])*z, P_TOL)
            fail("grid_sell_mode", sell-max(s["export_limit"])*(1-z), P_TOL)
            fail("simultaneous_buy_sell", min(b,sell), P_TOL)
            if k < start:
                fail("executed_prefix", max(abs(b-ref["grid"]["buy"][k]),
                                           abs(sell-ref["grid"]["sell"][k])), P_TOL)
            else:
                fail("bus_balance", abs(b-sell+
                     sum(plan["edges"][e]["q"][k] for e in s["load"])), P_TOL)
                cost += (s["buy_price"][k]*b-s["sell_price"][k]*sell)*dt
        total_signed = [sum(plan["edges"][e]["q"][k]-ref["edges"][e]["q"][k]
                            for e in s["load"]) for k in range(start,n)]
        d_vpp = sum(abs(value)*dt for value in total_signed)
        if total_cap is not None:
            fail("total_deviation_cap",d_vpp-total_cap,E_TOL)
        event_edge = s["event"]["edge"]
        participants = changed_devices-{event_edge}
        if participant_cap is not None:
            fail("participation", len(participants)-participant_cap, 0)
        if cost_cap is not None:
            fail("economic_cap", cost-cost_cap, C_TOL)
        return dict(valid=not errors, errors=errors, residuals=residuals,
                    cost=cost, dq=dq, d_edge=dq, d_vpp=d_vpp,
                    participants=sorted(participants),
                    n_adjust=len(participants), changed_interfaces=sorted(changed_q))
    except (KeyError, ValueError, TypeError, IndexError, OverflowError) as exc:
        errors.append(str(exc))
        return dict(valid=False, errors=errors, residuals=residuals)
