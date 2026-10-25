"""Ground-truth evaluation of the MergeProof engine (and of what the local AI adds).

Each seed turns the legacy sample into a fresh "consolidated" export: every ID re-keyed, rows shuffled,
headers renamed, dates/money/phones/emails/unit labels/statuses reformatted — then a random set of known
anomalies is injected. Because we created every change, we know exactly which pairings and findings are
correct, so we can score:

  * matching precision / recall per record type
  * anomaly recall and false alarms (findings nobody injected)
  * whether rent at risk and deposit totals come out to the exact expected cents
  * AI: rewritten work orders the rules can't pair vs. how often the local model's top suggestion is right,
    and how often it suggests something for a request that really is gone
  * column mapping on messy real-world header names, rules only vs. rules + AI suggestions

    python -m eval.harness --seeds 20            # from backend/; writes eval/results.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from datetime import date, timedelta
from pathlib import Path

from app import config
from app import normalize as nz
from app.ai import assist, embeddings
from app.engine import canonical, matching, metrics, rules
from app.ingest import suggest_mapping

AS_OF = "2026-09-30"
LABELS = {"old": "the legacy export", "new": "the consolidated export", "old_short": "legacy",
          "new_short": "consolidated", "old_system": "legacy", "new_system": "consolidated"}
FILES = {"properties": "bayview_properties.csv", "units": "bayview_units.csv", "tenants": "bayview_tenants.csv",
         "leases": "bayview_leases.csv", "deposits": "bayview_deposits.csv", "maintenance": "bayview_work_orders.csv"}

HEADERS = {
    "properties": {"property_id": ["Property ID", "Property Ref", "property_code"], "property_name": ["Property Name", "Building Name"],
                   "street_address": ["Street Address", "Address", "Address Line 1"], "city": ["City"], "state": ["State"]},
    "units": {"unit_id": ["Unit ID", "Unit Ref", "unit_code"], "property_id": ["Property ID", "Property Ref", "Building ID"],
              "unit_number": ["Unit", "Unit Number", "Apartment"], "bedrooms": ["Beds", "Bedrooms"]},
    "tenants": {"tenant_id": ["Tenant ID", "Resident ID", "resident_ref"], "name": ["Name", "Resident Name", "Tenant Name"],
                "email": ["Email", "Email Address"], "phone": ["Phone", "Mobile", "Phone Number"]},
    "leases": {"lease_id": ["Lease ID", "Lease Ref", "lease_number", "Agreement ID"],
               "tenant_id": ["Tenant ID", "Resident ID", "resident_ref"], "unit_id": ["Unit ID", "Unit Ref", "unit_code"],
               "start_date": ["Start Date", "Move-In Date", "lease_start", "Commencement Date"],
               "end_date": ["End Date", "Lease End", "Expiration Date"], "status": ["Status", "Lease Status"],
               "monthly_rent": ["Monthly Rent", "Base Rent", "Rent Amount", "Current Rent ($)"]},
    "deposits": {"deposit_id": ["Deposit ID", "Deposit Ref"], "lease_id": ["Lease ID", "Lease Ref"],
                 "deposit_balance": ["Deposit Held", "Deposit Balance", "Security Deposit"]},
    "maintenance": {"ticket_id": ["Work Order #", "Ticket ID", "Request ID"], "property_id": ["Property ID", "Property Ref"],
                    "unit_id": ["Unit ID", "Unit Ref"], "summary": ["Description", "Issue", "Summary"],
                    "priority": ["Priority", "Urgency"], "status": ["Status", "Work Order Status"],
                    "opened_on": ["Created", "Opened Date", "Date Opened"]},
}
STATUS = {"Active": ["Active", "Current", "Occupied"], "Expired": ["Expired", "Past", "Former"]}
T_STATUS = {"Open": ["Open", "New", "Submitted"], "In Progress": ["In Progress", "Assigned", "Scheduled"],
            "Completed": ["Completed", "Closed", "Done"]}
PRIORITY = {"Urgent": ["Urgent", "Emergency"], "High": ["High"], "Normal": ["Normal", "Medium", "Standard"],
            "Low": ["Low", "Minor"]}
PARAPHRASE = {
    "Dishwasher not draining": "Dishwasher won't drain - water sitting in the bottom",
    "Bathroom exhaust fan noisy": "Loud rattling from the bathroom vent fan",
    "Front door lock sticking": "Entry door deadbolt hard to turn",
    "Smoke detector chirping": "Smoke alarm beeping every minute, needs battery",
    "Hallway light fixture out": "Hall ceiling light not working",
    "Bedroom window will not latch": "Bedroom window latch broken, won't close securely",
    "Garbage disposal jammed": "Kitchen sink disposal stuck and humming",
    "Water heater pilot keeps going out": "No hot water - heater pilot light keeps dying",
    "Slow leak under bathroom sink": "Bathroom vanity pipe dripping underneath",
    "Pest control request - ants": "Ants in the kitchen, tenant requests pest treatment",
    "Mold spotting on bathroom ceiling": "Mould on bathroom ceiling above shower",
    "Intercom buzzer not working": "Door buzzer / intercom dead",
    "Refrigerator not cooling": "Fridge warm, food spoiling",
    "Closet door off track": "Sliding closet door came off its rail",
    "Toilet running constantly": "Toilet keeps running after flushing",
    "Balcony door seal worn": "Draft through balcony door, weatherstrip worn",
    "Oven igniter clicking": "Stove igniter keeps clicking, burner won't light",
    "Shower drain clogged": "Shower water not draining, clogged",
    "Thermostat display blank": "Thermostat screen dead, no display",
    "Ceiling stain after rain": "Water stain on ceiling appeared after the storm",
    "Kitchen outlet not working": "Kitchen power socket has no power",
    "Mailbox key replacement": "Tenant lost mailbox key, needs a new one",
    "Washer leaves water in drum": "Washing machine not draining fully",
    "Blinds broken in living room": "Living room window blinds broken",
    "No heat - bedroom radiator cold": "Heating not working, bedroom radiator stays cold",
    "Leaking kitchen faucet": "Kitchen tap dripping constantly",
}
ANOMALIES = ["drop_lease", "rent_down", "rent_up", "deactivate", "end_change", "orphan_unit", "wrong_unit",
             "drop_deposit", "deposit_change", "dup_tenant", "drop_open_ticket", "close_open_ticket", "rewrite_ticket"]

# Messy header names seen in real exports, with the field a person would map them to (None = shouldn't map).
HEADER_BENCH = {
    "leases": {"Monthly Charge Amt": "monthly_rent", "Resident Moved In": "start_date", "Lease Expires On": "end_date",
               "Occupant": "tenant_id", "Recurring Charge": "monthly_rent", "Lease Commencement": "start_date",
               "Apartment Ref": "unit_id", "Agreement No": "lease_id", "Occupancy State": "status",
               "Notes": None, "Phone": None, "Pet Fee": None, "Parking Spot": None},
    "deposits": {"Escrow Held": "balance", "Security Held Amt": "balance", "Tenancy Ref": "lease_id", "Comments": None},
    "maintenance": {"Problem Reported": "summary", "Logged On": "opened_date", "Urgency Level": "priority",
                    "Job Number": "ticket_id", "Work Status": "status", "Vendor": None, "Cost": None},
    "tenants": {"Renter Full Name": "full_name", "E-mail Addr": "email", "Cell #": "phone", "Renter No": "tenant_id",
                "Emergency Contact": None},
}


def load_before() -> dict[str, list[dict]]:
    out = {}
    for ent, name in FILES.items():
        with (config.SAMPLE_DIR / "before" / name).open(encoding="utf-8") as fh:
            out[ent] = list(csv.DictReader(fh))
    return out


def _fmt_date(rng: random.Random, iso: str) -> str:
    d = date.fromisoformat(iso)
    return rng.choice([d.isoformat(), d.strftime("%m/%d/%Y"), d.strftime("%b %d, %Y").replace(" 0", " ")])


def _fmt_money(rng: random.Random, cents: int) -> str:
    v = cents / 100
    return rng.choice([f"{v:.2f}", f"${v:,.2f}", f"{v:,.2f}", f"USD {v:.0f}" if cents % 100 == 0 else f"{v:.2f}"])


def _fmt_phone(rng: random.Random, p: str) -> str:
    d = "".join(ch for ch in p if ch.isdigit())[-10:]
    return rng.choice([f"({d[:3]}) {d[3:6]}-{d[6:]}", f"{d[:3]}.{d[3:6]}.{d[6:]}", f"+1 {d[:3]}-{d[3:6]}-{d[6:]}", d])


def _fmt_unit(scheme: int, label: str) -> str:
    lab = nz.unit_label(label) or label
    return [lab, f"Apt {lab}", f"#{lab[:-1]}-{lab[-1]}" if lab[-1:].isalpha() else f"#{lab}", f"Unit {lab}"][scheme]


def _cents(v: str) -> int:
    return nz.parse_money(v) or 0


def perturb(seed: int, before: dict[str, list[dict]], hard: bool = True,
            anomalies: bool = True) -> tuple[dict[str, list[dict]], dict]:
    """anomalies=False gives a *clean* migration: the same re-keying and format noise, nothing injected."""
    rng = random.Random(seed)
    B = {k: [dict(r) for r in v] for k, v in before.items()}
    pid = {r["property_id"]: f"EV-P{seed}-{i:02d}" for i, r in enumerate(rng.sample(B["properties"], len(B["properties"])))}
    uid = {r["unit_id"]: f"EV-U{seed}-{i:04d}" for i, r in enumerate(rng.sample(B["units"], len(B["units"])))}
    tid = {r["tenant_id"]: f"EV-T{seed}-{i:04d}" for i, r in enumerate(rng.sample(B["tenants"], len(B["tenants"])))}
    lid = {r["lease_id"]: f"EV-L{seed}-{i:04d}" for i, r in enumerate(rng.sample(B["leases"], len(B["leases"])))}
    did = {r["deposit_id"]: f"EV-D{seed}-{i:04d}" for i, r in enumerate(rng.sample(B["deposits"], len(B["deposits"])))}
    wid = {r["ticket_id"]: f"EV-W{seed}-{i:04d}" for i, r in enumerate(rng.sample(B["maintenance"], len(B["maintenance"])))}
    unit_scheme = {p["property_id"]: rng.randrange(4) for p in B["properties"]}
    unit_prop = {u["unit_id"]: u["property_id"] for u in B["units"]}

    leases_by_unit: dict[str, list[dict]] = {}
    for l in B["leases"]:
        leases_by_unit.setdefault(l["unit_id"], []).append(l)
    deposit_of = {d["lease_id"]: d for d in B["deposits"]}
    ordinary = [l for l in B["leases"] if l["status"] == "Active" and len(leases_by_unit[l["unit_id"]]) == 1]
    rng.shuffle(ordinary)
    vacant_units = [u for u in B["units"] if u["unit_id"] not in leases_by_unit]
    open_tickets = [t for t in B["maintenance"] if t["status"] != "Completed"]
    rng.shuffle(open_tickets)

    plan: dict[str, list[dict]] = {a: [] for a in ANOMALIES}
    for a in ANOMALIES:
        n = rng.randint(1, 2) if anomalies else 0
        for _ in range(n):
            if a in ("drop_open_ticket", "close_open_ticket", "rewrite_ticket"):
                if open_tickets:
                    plan[a].append(open_tickets.pop())
            elif a == "drop_deposit" or a == "deposit_change":
                cand = next((l for l in ordinary if l["lease_id"] in deposit_of), None)
                if cand:
                    ordinary.remove(cand)
                    plan[a].append(cand)
            elif a == "wrong_unit":
                cand = next((l for l in ordinary if any(unit_prop[v["unit_id"]] == unit_prop[l["unit_id"]]
                                                        for v in vacant_units)), None)
                if cand:
                    ordinary.remove(cand)
                    vac = next(v for v in vacant_units if unit_prop[v["unit_id"]] == unit_prop[cand["unit_id"]])
                    vacant_units.remove(vac)
                    plan[a].append({**cand, "_to": vac["unit_id"]})
            elif ordinary:
                plan[a].append(ordinary.pop())

    expected: set[tuple[str, str]] = set()
    rent_risk = deposit_gap = 0
    pairs: dict[str, dict] = {e: {} for e in FILES}
    dropped_leases = {l["lease_id"] for l in plan["drop_lease"]}
    for l in plan["drop_lease"]:
        expected.add(("L-001", l["lease_id"]))
        rent_risk += _cents(l["monthly_rent"])
        if l["lease_id"] in deposit_of and _cents(deposit_of[l["lease_id"]]["deposit_balance"]) > 0:
            expected.add(("D-001", l["lease_id"]))
            deposit_gap += _cents(deposit_of[l["lease_id"]]["deposit_balance"])
    lease_edit: dict[str, dict] = {}
    for l in plan["rent_down"]:
        delta = 25 * rng.randint(2, 20)
        lease_edit[l["lease_id"]] = {"monthly_rent": _cents(l["monthly_rent"]) - delta * 100}
        expected.add(("L-002", l["lease_id"]))
        rent_risk += delta * 100
    for l in plan["rent_up"]:
        lease_edit[l["lease_id"]] = {"monthly_rent": _cents(l["monthly_rent"]) + 25 * rng.randint(2, 20) * 100}
        expected.add(("L-003", l["lease_id"]))
    for l in plan["deactivate"]:
        lease_edit[l["lease_id"]] = {"status": "Expired"}
        expected.add(("L-004", l["lease_id"]))
        rent_risk += _cents(l["monthly_rent"])
    for l in plan["end_change"]:
        end = date.fromisoformat(l["end_date"])
        lease_edit[l["lease_id"]] = {"end_date": end.replace(year=end.year + rng.choice([-1, 1])).isoformat()}
        expected.add(("L-005", l["lease_id"]))
    for l in plan["orphan_unit"]:
        lease_edit[l["lease_id"]] = {"unit_ref": f"EV-U{seed}-9{rng.randrange(1000):03d}X"}
        expected.add(("L-009", l["lease_id"]))
    for l in plan["wrong_unit"]:
        lease_edit[l["lease_id"]] = {"unit_ref": uid[l["_to"]]}
        expected.add(("L-007", l["lease_id"]))
    drop_dep, dep_edit = set(), {}
    for l in plan["drop_deposit"]:
        drop_dep.add(l["lease_id"])
        expected.add(("D-001", l["lease_id"]))
        deposit_gap += _cents(deposit_of[l["lease_id"]]["deposit_balance"])
    for l in plan["deposit_change"]:
        delta = 100 * rng.choice([-1, 1]) * rng.randint(1, 30) * 10
        old = _cents(deposit_of[l["lease_id"]]["deposit_balance"])
        dep_edit[l["lease_id"]] = max(0, old + delta)
        expected.add(("D-002", l["lease_id"]))
        deposit_gap += abs(dep_edit[l["lease_id"]] - old)
    dup_tenants = {l["tenant_id"] for l in plan["dup_tenant"]}
    for t in dup_tenants:
        expected.add(("T-001", t))
    drop_tk = {t["ticket_id"] for t in plan["drop_open_ticket"]}
    for t in plan["drop_open_ticket"]:
        expected.add(("M-001", t["ticket_id"]))
    close_tk = {t["ticket_id"] for t in plan["close_open_ticket"]}
    for t in plan["close_open_ticket"]:
        expected.add(("M-002", t["ticket_id"]))
    rewrite_tk = {t["ticket_id"] for t in plan["rewrite_ticket"]}
    for t in plan["rewrite_ticket"]:
        expected.add(("M-001", t["ticket_id"]))  # what the rules alone can say; AI may suggest the counterpart

    A: dict[str, list[dict]] = {}
    H = {e: {f: rng.choice(opts) for f, opts in HEADERS[e].items()} for e in HEADERS}
    A["properties"] = []
    for p in B["properties"]:
        addr = p["street_address"]
        if rng.random() < 0.5:
            addr = addr.replace("Avenue", "Ave.").replace("Street", "St").replace("Lane", "Ln").replace("Court", "Ct")
        A["properties"].append({H["properties"]["property_id"]: pid[p["property_id"]],
                                H["properties"]["property_name"]: p["property_name"],
                                H["properties"]["street_address"]: addr, H["properties"]["city"]: p["city"],
                                H["properties"]["state"]: p["state"]})
        pairs["properties"][p["property_id"]] = pid[p["property_id"]]
    A["units"] = []
    for u in B["units"]:
        A["units"].append({H["units"]["unit_id"]: uid[u["unit_id"]], H["units"]["property_id"]: pid[u["property_id"]],
                           H["units"]["unit_number"]: _fmt_unit(unit_scheme[u["property_id"]], u["unit_number"]),
                           H["units"]["bedrooms"]: u["bedrooms"]})
        pairs["units"][u["unit_id"]] = uid[u["unit_id"]]
    A["tenants"] = []
    noise = {"blank_email": 0, "blank_phone": 0, "blank_both": 0, "name_typo": 0}
    for t in B["tenants"]:
        name = f"{t['first_name']} {t['last_name']}"
        email = t["email"] if rng.random() < 0.7 else t["email"].title()
        phone = _fmt_phone(rng, t["phone"])
        if hard and t["tenant_id"] not in dup_tenants:
            r = rng.random()
            if r < 0.08:
                email, noise["blank_email"] = "", noise["blank_email"] + 1
            elif r < 0.13:
                phone, noise["blank_phone"] = "", noise["blank_phone"] + 1
            elif r < 0.16:
                email, phone, noise["blank_both"] = "", "", noise["blank_both"] + 1
            if rng.random() < 0.05 and len(t["last_name"]) > 4:  # adjacent-letter typo in the surname
                ln = t["last_name"]
                i = rng.randrange(1, len(ln) - 2)
                name = f"{t['first_name']} {ln[:i]}{ln[i + 1]}{ln[i]}{ln[i + 2:]}"
                noise["name_typo"] += 1
        row = {H["tenants"]["tenant_id"]: tid[t["tenant_id"]], H["tenants"]["name"]: name,
               H["tenants"]["email"]: email, H["tenants"]["phone"]: phone}
        A["tenants"].append(row)
        pairs["tenants"][t["tenant_id"]] = {tid[t["tenant_id"]]}
        if t["tenant_id"] in dup_tenants:
            dup = f"{tid[t['tenant_id']]}-DUP"
            A["tenants"].append({**row, H["tenants"]["tenant_id"]: dup, H["tenants"]["email"]: t["email"].upper(),
                                 H["tenants"]["phone"]: _fmt_phone(rng, t["phone"])})
            pairs["tenants"][t["tenant_id"]].add(dup)
    A["leases"] = []
    for l in B["leases"]:
        if l["lease_id"] in dropped_leases:
            pairs["leases"][l["lease_id"]] = None
            continue
        e = lease_edit.get(l["lease_id"], {})
        rent = e.get("monthly_rent", _cents(l["monthly_rent"]))
        status = e.get("status", l["status"])
        tenant_ref = tid[l["tenant_id"]]
        if l["tenant_id"] in dup_tenants and rng.random() < 0.5:
            tenant_ref = f"{tenant_ref}-DUP"
        A["leases"].append({H["leases"]["lease_id"]: lid[l["lease_id"]], H["leases"]["tenant_id"]: tenant_ref,
                            H["leases"]["unit_id"]: e.get("unit_ref", uid[l["unit_id"]]),
                            H["leases"]["start_date"]: _fmt_date(rng, l["start_date"]),
                            H["leases"]["end_date"]: _fmt_date(rng, e.get("end_date", l["end_date"])),
                            H["leases"]["status"]: rng.choice(STATUS[status]),
                            H["leases"]["monthly_rent"]: _fmt_money(rng, rent)})
        pairs["leases"][l["lease_id"]] = lid[l["lease_id"]]
    A["deposits"] = []
    for d in B["deposits"]:
        if d["lease_id"] in dropped_leases or d["lease_id"] in drop_dep:
            continue
        bal = dep_edit.get(d["lease_id"], _cents(d["deposit_balance"]))
        A["deposits"].append({H["deposits"]["deposit_id"]: did[d["deposit_id"]], H["deposits"]["lease_id"]: lid[d["lease_id"]],
                              H["deposits"]["deposit_balance"]: _fmt_money(rng, bal)})
    A["maintenance"] = []
    for t in B["maintenance"]:
        if t["ticket_id"] in drop_tk:
            pairs["maintenance"][t["ticket_id"]] = None
            continue
        status = "Completed" if t["ticket_id"] in close_tk else t["status"]
        summary, unit_ref, opened = t["summary"], uid[t["unit_id"]], date.fromisoformat(t["opened_on"])
        if t["ticket_id"] in rewrite_tk:  # retyped description, logged at property level, entered a few days later
            summary = PARAPHRASE.get(t["summary"], t["summary"])
            unit_ref, opened = "", opened + timedelta(days=rng.randint(1, 6))
        elif rng.random() < 0.25:
            summary = summary.lower() + "."
        A["maintenance"].append({H["maintenance"]["ticket_id"]: wid[t["ticket_id"]],
                                 H["maintenance"]["property_id"]: pid[t["property_id"]],
                                 H["maintenance"]["unit_id"]: unit_ref, H["maintenance"]["summary"]: summary,
                                 H["maintenance"]["priority"]: rng.choice(PRIORITY[t["priority"]]),
                                 H["maintenance"]["status"]: rng.choice(T_STATUS[status]),
                                 H["maintenance"]["opened_on"]: _fmt_date(rng, opened.isoformat())})
        pairs["maintenance"][t["ticket_id"]] = None if t["ticket_id"] in rewrite_tk else wid[t["ticket_id"]]
    for ent in A:
        rng.shuffle(A[ent])
    truth = {"expected": expected, "rent_risk": rent_risk, "deposit_gap": deposit_gap, "pairs": pairs,
             "rewrites": {t["ticket_id"]: wid[t["ticket_id"]] for t in plan["rewrite_ticket"]},
             "dropped_tickets": drop_tk, "plan": {a: len(v) for a, v in plan.items()}, "noise": noise}
    return A, truth


def _snap(side: str, entity: str, rows: list[dict]) -> canonical.SnapshotInput:
    headers = list(rows[0].keys())
    mapping = {k: v["column"] for k, v in suggest_mapping(entity, headers).items()}
    return canonical.SnapshotInput(f"{side}-{entity}", side, entity, f"{side}_{entity}.csv", headers,
                                   [(i + 2, r) for i, r in enumerate(rows)], mapping)


def run_engine(before: dict, after: dict, ai) -> dict:
    t0 = time.perf_counter()
    inputs = [_snap("before", e, r) for e, r in before.items()] + [_snap("after", e, r) for e, r in after.items()]
    unmapped = [f"{s.side}:{s.entity}:{f.key}" for s in inputs for f in __import__("app.schema", fromlist=["ENTITIES"])
                .ENTITIES[s.entity].fields if f.required and f.key not in s.mapping]
    recs, dq, _ = canonical.build(inputs, AS_OF)
    S = matching.Side("before", {e: recs.get(("before", e), []) for e in FILES})
    T = matching.Side("after", {e: recs.get(("after", e), []) for e in FILES})
    M = {"properties": matching.match_properties(S, T, {})}
    M["units"] = matching.match_units(S, T, M["properties"], {})
    M["tenants"] = matching.match_tenants(S, T, {})
    M["leases"] = matching.match_leases(S, T, M["units"], M["tenants"], {}, M["properties"])
    matching.corroborate_tenants(S, T, M["tenants"], M["leases"])
    M["maintenance"] = matching.match_tickets(S, T, M["properties"], M["units"], {})
    ctx = rules.run_all(S, T, M, dq, LABELS, AS_OF, ai=ai)
    rows = [{**f, "id": f["anchor"], "status": "open", "active": 1, "needs_rereview": 0} for f in ctx.findings]
    rent, _, _ = metrics.attribute_rent(rows)
    deposit = sum(f["deposit_cents"] for f in ctx.findings)
    return {"findings": ctx.findings, "M": M, "S": S, "T": T, "rent": rent, "deposit": deposit, "unmapped": unmapped,
            "ms": (time.perf_counter() - t0) * 1000}


def score(truth: dict, res: dict) -> dict:
    found = {(f["rule_id"], f["anchor"].split("|", 1)[1]) for f in res["findings"]}
    review = {(f["rule_id"], f["anchor"].split("|", 1)[1]) for f in res["findings"] if f["confidence"] == "needs_review"}
    expected = truth["expected"]
    extra = found - expected
    missed = expected - found
    review_keys = {k for _, k in review}
    out = {"expected": len(expected), "hit": len(expected & found),
           "false_alarms": sorted(extra - review), "routed_to_review": sorted(extra & review),
           "missed": sorted(m for m in missed if m[1] not in review_keys),
           "missed_but_in_review": sorted(m for m in missed if m[1] in review_keys), "rent_exact": res["rent"] == truth["rent_risk"],
           "deposit_exact": res["deposit"] == truth["deposit_gap"], "unmapped": res["unmapped"], "ms": res["ms"]}
    match = {}
    for ent, truth_pairs in truth["pairs"].items():
        if ent == "deposits":
            continue
        R = res["M"][ent]
        tp = fp = 0
        for s, m in R.matches.items():
            want = truth_pairs.get(s)
            ok = (m.t in want) if isinstance(want, set) else (m.t == want)
            tp += ok
            fp += not ok
        should = sum(1 for v in truth_pairs.values() if v)
        match[ent] = {"tp": tp, "fp": fp, "should": should}
    out["matching"] = match
    # AI on rewritten / dropped work orders
    sugg = {f["anchor"].split("|", 1)[1]: [c["target_key"] for c in f["candidates"] if c.get("origin") == "ai"]
            for f in res["findings"] if f["rule_id"] == "M-001"}
    out["ai"] = {"rewrites": len(truth["rewrites"]),
                 "top1": sum(1 for s, t in truth["rewrites"].items() if sugg.get(s, [None])[:1] == [t]),
                 "any": sum(1 for s, t in truth["rewrites"].items() if t in sugg.get(s, [])),
                 "dropped": len(truth["dropped_tickets"]),
                 "spurious": sum(1 for s in truth["dropped_tickets"] if sugg.get(s))}
    return out


def header_bench(ai) -> dict:
    tally = {"correct": 0, "wrong": 0, "abstained": 0, "total": 0}
    rows = []
    for ent, cases in HEADER_BENCH.items():
        headers = list(cases)
        rules_map = {k: v["column"] for k, v in suggest_mapping(ent, headers).items()}
        sugg = assist.header_suggestions(ai, ent, headers, rules_map) if ai else {}
        chosen = {col: f for f, col in rules_map.items()} | {s["column"]: f for f, s in sugg.items()}
        for h, want in cases.items():
            got = chosen.get(h)
            ok = got == want
            tally["total"] += 1
            tally["correct" if ok else ("abstained" if got is None else "wrong")] += 1
            rows.append({"entity": ent, "header": h, "expected": want, "got": got,
                         "by": "ai" if h in {s["column"] for s in sugg.values()} else ("rules" if got else None)})
    tally["accuracy"] = round(tally["correct"] / tally["total"], 3)
    return {"summary": tally, "rows": rows}


def evaluate(seeds: int, use_ai: bool, start: int = 1) -> dict:
    """Seeds start..start+seeds-1. Seeds 1-30 were used while developing the engine; 1001+ are a holdout."""
    before = load_before()
    ai = embeddings.get() if use_ai else None
    runs = []
    for seed in range(start, start + seeds):
        after, truth = perturb(seed, before)
        runs.append(score(truth, run_engine(before, after, ai)))
    exp = sum(r["expected"] for r in runs)
    hit = sum(r["hit"] for r in runs)
    fa = sum(len(r["false_alarms"]) for r in runs)
    routed = sum(len(r["routed_to_review"]) for r in runs)
    missed_review = sum(len(r["missed_but_in_review"]) for r in runs)
    matching_out = {}
    for ent in runs[0]["matching"]:
        tp = sum(r["matching"][ent]["tp"] for r in runs)
        fp = sum(r["matching"][ent]["fp"] for r in runs)
        should = sum(r["matching"][ent]["should"] for r in runs)
        matching_out[ent] = {"precision": round(tp / (tp + fp), 4) if tp + fp else None,
                             "recall": round(tp / should, 4) if should else None, "pairs": should}
    ai_tot = {k: sum(r["ai"][k] for r in runs) for k in runs[0]["ai"]}
    return {
        "seeds": seeds, "first_seed": start, "ai_model": ai.model_id if ai else None,
        "anomalies": {"injected": exp, "detected": hit, "recall": round(hit / exp, 4) if exp else None,
                      "false_alarms": fa, "precision": round(hit / (hit + fa), 4) if hit + fa else None,
                      "routed_to_review": routed, "missed_but_flagged_for_review": missed_review,
                      "silently_missed": exp - hit - missed_review,
                      "missed_examples": [m for r in runs for m in r["missed"]][:10],
                      "false_alarm_examples": [m for r in runs for m in r["false_alarms"]][:10]},
        "matching": matching_out,
        "money": {"rent_at_risk_exact": sum(r["rent_exact"] for r in runs), "deposit_exact": sum(r["deposit_exact"] for r in runs),
                  "of": seeds},
        "mapping_failures": sum(len(r["unmapped"]) for r in runs),
        "ai_work_orders": ai_tot,
        "ms_per_audit": round(statistics.mean(r["ms"] for r in runs), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=str(Path(__file__).with_name("results.json")))
    args = ap.parse_args()
    rules_only = evaluate(args.seeds, use_ai=False)
    ai_avail = embeddings.get() is not None
    with_ai = evaluate(args.seeds, use_ai=True) if ai_avail else None
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": config.ENGINE_VERSION,
        "rules_only": rules_only, "with_ai": with_ai, "ai_status": embeddings.status(),
        "headers": {"rules_only": header_bench(None), "with_ai": header_bench(embeddings.get()) if ai_avail else None},
    }
    Path(args.out).write_text(json.dumps(result, indent=1, default=list), encoding="utf-8")
    ro = rules_only
    an = ro["anomalies"]
    print(f"seeds={args.seeds}  injected={an['injected']}  recall={an['recall']}  wrong_claims={an['false_alarms']}  "
          f"routed_to_review={an['routed_to_review']}  silently_missed={an['silently_missed']}  "
          f"rent exact {ro['money']['rent_at_risk_exact']}/{args.seeds}  "
          f"deposit exact {ro['money']['deposit_exact']}/{args.seeds}  {ro['ms_per_audit']}ms/audit")
    for ent, m in ro["matching"].items():
        print(f"  {ent:12s} precision={m['precision']} recall={m['recall']} ({m['pairs']} true pairs)")
    if with_ai:
        a = with_ai["ai_work_orders"]
        print(f"AI work orders: rewritten={a['rewrites']} top1={a['top1']} any={a['any']} | dropped={a['dropped']} "
              f"spurious_suggestions={a['spurious']}  (money identical: "
              f"{with_ai['money'] == ro['money'] and with_ai['anomalies']['detected'] == ro['anomalies']['detected']})")
    h0 = result["headers"]["rules_only"]["summary"]
    h1 = (result["headers"]["with_ai"] or {}).get("summary")
    print(f"messy headers: rules only {h0['correct']}/{h0['total']} correct, {h0['wrong']} wrong"
          + (f" | rules + AI {h1['correct']}/{h1['total']} correct, {h1['wrong']} wrong" if h1 else ""))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
