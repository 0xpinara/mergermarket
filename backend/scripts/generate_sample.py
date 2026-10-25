#!/usr/bin/env python3
"""Generate the deterministic synthetic "Cedar x Bayview" sample exports.

Everything produced here is fictional: people, streets, cities, amounts and
identifiers. Phone numbers use the 555-01xx block reserved for fiction and all
emails use example.com.

The legacy ("before") export is Bayview's old system. The consolidated
("after") export is Cedar's system after migration, with different column
names, ID schemes and value formats, plus a small set of deliberate anomalies
documented in SCENARIO below. Run this script to regenerate the CSVs; the
output is byte-for-byte stable because the RNG is seeded and every choice is
made in a fixed order.
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "sample_data"
SEED = 20261024
AS_OF = date(2026, 9, 30)

# code, name, legacy street, consolidated street, city, floors, units per floor, rent base
PROPERTIES = [
    ("01", "Marigold Flats", "1450 Marigold Avenue", "1450 Marigold Ave.", "Alder Bay", 5, 6, 2050),
    ("02", "Juniper Terrace", "88 Juniper Terrace", "88 Juniper Terrace", "Alder Bay", 5, 4, 2150),
    ("03", "Larkspur Commons", "310 Larkspur Lane", "310 Larkspur Lane", "Alder Bay", 4, 6, 1800),
    ("04", "Quarry Row Lofts", "17 Quarry Row", "17 Quarry Row", "Alder Bay", 6, 3, 2450),
    ("05", "Tidewater House", "2201 Tidewater Street", "2201 Tidewater Street", "Alder Bay", 6, 5, 1900),
    ("06", "Sorrel Court", "54 Sorrel Court", "54 Sorrel Court", "Alder Bay", 4, 6, 1750),
    ("07", "Halyard Gardens", "905 Halyard Way", "905 Halyard Way", "Port Wren", 4, 7, 1700),
    ("08", "Wren Street Six", "6 Wren Street", "6 Wren Street", "Port Wren", 3, 4, 1950),
]

# (property code, unit label) -> scenario
SCENARIO = {
    ("02", "4B"): "missing_lease",       # active lease absent from Cedar; tenant migrated, lease + deposit did not
    ("04", "5A"): "rent_decrease",       # $2,500 -> $2,050
    ("05", "3A"): "deposit_missing",     # $1,800 deposit absent
    ("06", "1B"): "duplicate_tenant",    # resident created twice in Cedar
    ("06", "2A"): "orphan_unit",         # Cedar lease points at a unit ref that does not exist
    ("03", "2C"): "ambiguous",           # two "Alex Kim" candidates, neither conclusive
    ("03", "2D"): "vacant",
    ("07", "1C"): "wrong_unit",          # Cedar lease attached to 1D instead of 1C
    ("07", "1D"): "vacant",
    ("07", "3D"): "inactive_and_rent",   # active -> "Past", and rent $1,725 -> $1,575 (counted once)
    ("08", "2B"): "rent_increase",       # $1,950 -> $2,095 (review, not "at risk")
    ("08", "3A"): "term_change",         # end date 2027-03-31 -> 2026-03-31
    ("08", "1C"): "deposit_mismatch",    # $1,650 -> $1,560
    ("01", "5F"): "vacant_new_lease",    # vacant at cutover, new Cedar lease afterwards
}

NAMED = {
    "missing_lease": ("Dana", "Whitfield", 225000, 225000),
    "rent_decrease": ("Theo", "Alvarez", 250000, 250000),
    "deposit_missing": ("Mina", "Okafor", 190000, 180000),
    "duplicate_tenant": ("Priya", "Raman", 172500, 172500),
    "orphan_unit": ("Grace", "Liu", 169500, 150000),
    "ambiguous": ("Alex", "Kim", 187500, 187500),
    "wrong_unit": ("Jun", "Park", 162500, 160000),
    "inactive_and_rent": ("Omar", "Haddad", 172500, 172500),
    "rent_increase": ("Lena", "Marsh", 195000, 195000),
    "term_change": ("Felix", "Brandt", 189500, 180000),
    "deposit_mismatch": ("Ines", "Duarte", 182500, 165000),
}

FIRST = [
    "Avery", "Blake", "Camila", "Darius", "Elena", "Farah", "Gabriel", "Hana", "Isaac", "Jasmine",
    "Kofi", "Leah", "Mateo", "Nadia", "Oliver", "Paloma", "Quinn", "Rafael", "Sana", "Tomas",
    "Uma", "Victor", "Wendy", "Xavier", "Yara", "Zane", "Amara", "Bruno", "Celeste", "Dmitri",
    "Esme", "Finn", "Greta", "Hugo", "Ivy", "Joel", "Keiko", "Luca", "Maya", "Nico",
    "Opal", "Pedro", "Rhea", "Silas", "Talia", "Ugo", "Vera", "Wes", "Yusuf", "Zoe",
    "Anika", "Bodhi", "Clara", "Dev", "Eli", "Freya", "Gideon", "Holly", "Imani", "Jonah",
]
LAST = [
    "Abernathy", "Bello", "Castellanos", "Dimitrov", "Eriksen", "Fairweather", "Gallo", "Halvorsen",
    "Iwata", "Jaramillo", "Kowalczyk", "Lindgren", "Mbeki", "Nakamura", "Oyelaran", "Pellegrino",
    "Quiroga", "Rasmussen", "Sorensen", "Tanaka", "Underwood", "Valdivia", "Whitlock", "Yilmaz",
    "Zelenko", "Achebe", "Brennan", "Calloway", "Delacroix", "Esposito", "Ferreira", "Grünewald",
    "Hargrove", "Ibarra", "Jovanovic", "Kearney", "Lombardi", "Moreau", "Novak", "Ostrowski",
    "Prakash", "Renner", "Sato", "Thorne", "Ulloa", "Vance", "Wexler", "Yoon", "Zamora", "Ashby",
]

ISSUES = [
    ("Dishwasher not draining", "Normal"), ("Bathroom exhaust fan noisy", "Low"),
    ("Front door lock sticking", "Normal"), ("Smoke detector chirping", "High"),
    ("Hallway light fixture out", "Low"), ("Bedroom window will not latch", "Normal"),
    ("Garbage disposal jammed", "Normal"), ("Water heater pilot keeps going out", "High"),
    ("Slow leak under bathroom sink", "Normal"), ("Pest control request - ants", "Low"),
    ("Mold spotting on bathroom ceiling", "High"), ("Intercom buzzer not working", "Low"),
    ("Refrigerator not cooling", "High"), ("Closet door off track", "Low"),
    ("Toilet running constantly", "Normal"), ("Balcony door seal worn", "Low"),
    ("Oven igniter clicking", "Normal"), ("Shower drain clogged", "Normal"),
    ("Thermostat display blank", "Normal"), ("Ceiling stain after rain", "High"),
    ("Kitchen outlet not working", "High"), ("Mailbox key replacement", "Low"),
    ("Washer leaves water in drum", "Normal"), ("Blinds broken in living room", "Low"),
]

TGT_PRIORITY = {"Urgent": "Emergency", "High": "High", "Normal": "Medium", "Low": "Low"}
TGT_TICKET_STATUS = {"Open": "New", "In Progress": "In Progress", "Completed": "Closed"}


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def us_date(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def dollars(cents: int) -> str:
    return f"{cents / 100:.2f}"


def money_fmt(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def main() -> None:
    rng = random.Random(SEED)

    phone_pool = [f"{ac}{n:04d}" for ac in ("415", "510", "628", "650") for n in range(100, 200)]
    reserved = {"4150142", "4150199", "5100123"}
    phone_pool = [p for p in phone_pool if p[:3] + p[-4:] not in reserved]
    rng.shuffle(phone_pool)

    reserved_names = {(f, l) for f, l, *_ in NAMED.values()} | {("Rosa", "Lindqvist")}
    name_pool = [(f, l) for f in FIRST for l in LAST if (f, l) not in reserved_names]
    rng.shuffle(name_pool)

    def src_phone(p: str) -> str:
        return f"({p[:3]}) 555-{p[-4:]}"

    def tgt_phone(p: str, style: int) -> str:
        return [f"{p[:3]}.555.{p[-4:]}", f"+1 {p[:3]}-555-{p[-4:]}", f"{p[:3]}555{p[-4:]}"][style % 3]

    # ---------------------------------------------------------------- legacy model
    props, units, tenants, leases, deposits, tickets = [], [], [], [], [], []
    unit_seq, tenant_seq, lease_seq, dep_seq, ticket_seq = 1, 1, 1001, 1, 3001

    def new_tenant(first: str, last: str, phone: str, email: str | None = None) -> dict:
        nonlocal tenant_seq
        t = {
            "tenant_id": f"BV-T-{tenant_seq:04d}", "first_name": first, "last_name": last,
            "email": email or f"{first}.{last}@example.com".lower().replace("ü", "u"),
            "phone": phone,
        }
        tenant_seq += 1
        tenants.append(t)
        return t

    for code, name, street, _tstreet, city, floors, per_floor, base in PROPERTIES:
        props.append({"property_id": f"BV-P-{code}", "property_name": name, "street_address": street,
                      "city": city, "state": "CA"})
        for floor in range(1, floors + 1):
            for idx in range(per_floor):
                label = f"{floor}{chr(65 + idx)}"
                beds = [0, 1, 1, 2, 2, 3][(idx + floor) % 6]
                # Bayview also exported a door code ("MF-4B"). It is unique, but nothing references it —
                # leases and work orders point at unit_id. A name-based mapper would pick it for a target
                # "unit_code" key; only relational evidence shows it is the wrong key.
                u = {"unit_id": f"BV-U-{unit_seq:04d}", "property_id": f"BV-P-{code}",
                     "unit_number": f"Apt {label}" if code == "01" else label,
                     "unit_code": f"{''.join(w[0] for w in name.split()[:2]).upper()}-{label}",
                     "bedrooms": str(beds), "_code": code, "_label": label}
                unit_seq += 1
                units.append(u)

    for u in units:
        scen = SCENARIO.get((u["_code"], u["_label"]))
        u["_scenario"] = scen
        if scen in ("vacant", "vacant_new_lease"):
            u["_occupied"] = False
        elif scen:
            u["_occupied"] = True
        else:
            u["_occupied"] = rng.random() > 0.075

    for u in units:
        if not u["_occupied"]:
            continue
        scen = u["_scenario"]
        base = next(p[7] for p in PROPERTIES if p[0] == u["_code"])
        if scen in NAMED:
            first, last, rent, dep = NAMED[scen]
            if scen == "ambiguous":
                t = new_tenant(first, last, "(415) 555-0142")
            elif scen == "duplicate_tenant":
                t = new_tenant(first, last, "(510) 555-0123")
            else:
                t = new_tenant(first, last, src_phone(phone_pool.pop()))
        else:
            first, last = name_pool.pop()
            t = new_tenant(first, last, src_phone(phone_pool.pop()))
            rent = (base + int(u["bedrooms"]) * 175 + rng.randrange(-6, 9) * 25) * 100
            dep = rent if rng.random() < 0.55 else rng.choice([150000, 180000, 200000])
            if rng.random() < 0.08:
                dep = 0  # deposit waived / surety bond
        if scen == "ambiguous":
            start = date(2025, 11, 1)
        elif scen == "term_change":
            start = date(2026, 4, 1)
        else:
            start = date(2025, 1, 1) + timedelta(days=31 * rng.randrange(0, 21))
            start = start.replace(day=1)
        term = 12
        end = add_months(start, term) - timedelta(days=1)
        if end < date(2026, 10, 31):
            term = 24
            end = add_months(start, term) - timedelta(days=1)
        lease = {"lease_id": f"BV-L-{lease_seq}", "tenant_id": t["tenant_id"], "unit_id": u["unit_id"],
                 "start_date": start.isoformat(), "end_date": end.isoformat(), "status": "Active",
                 "monthly_rent": dollars(rent), "_unit": u, "_tenant": t, "_rent": rent, "_scenario": scen,
                 "_start": start, "_end": end}
        lease_seq += 1
        leases.append(lease)
        if dep:
            deposits.append({"deposit_id": f"BV-D-{dep_seq:04d}", "lease_id": lease["lease_id"],
                             "tenant_id": t["tenant_id"], "deposit_balance": dollars(dep),
                             "held_since": start.isoformat(), "_lease": lease, "_amount": dep})
            dep_seq += 1

    # Historical (expired) leases on a dozen ordinary units; deposits already refunded.
    ordinary = [l for l in leases if not l["_scenario"]]
    history_units = rng.sample(ordinary, 12)
    history = []
    for cur in sorted(history_units, key=lambda l: l["lease_id"]):
        first, last = name_pool.pop()
        t = new_tenant(first, last, src_phone(phone_pool.pop()))
        end = cur["_start"] - timedelta(days=1)
        start = add_months(end.replace(day=1), -11)
        rent = cur["_rent"] - 7500
        hl = {"lease_id": f"BV-L-{lease_seq}", "tenant_id": t["tenant_id"], "unit_id": cur["unit_id"],
              "start_date": start.isoformat(), "end_date": end.isoformat(), "status": "Expired",
              "monthly_rent": dollars(rent), "_unit": cur["_unit"], "_tenant": t, "_rent": rent,
              "_scenario": "history", "_start": start, "_end": end}
        lease_seq += 1
        history.append(hl)
    leases.extend(history)
    unmigrated_history = history[7]["lease_id"]

    # Work orders
    occupied_units = [u for u in units if u["_occupied"]]
    used = set()
    for i in range(68):
        while True:
            u = rng.choice(occupied_units)
            summary, prio = rng.choice(ISSUES)
            if (u["unit_id"], summary) not in used and u["_scenario"] is None:
                used.add((u["unit_id"], summary))
                break
        opened = date(2026, 6, 1) + timedelta(days=rng.randrange(0, 120))
        if opened > date(2026, 8, 25):
            status = rng.choice(["Open", "Open", "In Progress"])
        else:
            status = "Completed" if rng.random() < 0.88 else "In Progress"
        tickets.append({"ticket_id": f"BV-WO-{ticket_seq}", "property_id": u["property_id"],
                        "unit_id": u["unit_id"], "summary": summary, "priority": prio, "status": status,
                        "opened_on": opened.isoformat(), "_unit": u, "_scenario": None, "_opened": opened})
        ticket_seq += 1

    def unit_of(code: str, label: str) -> dict:
        return next(u for u in units if u["_code"] == code and u["_label"] == label)

    for code, label, summary, prio, status, opened, scen in [
        ("05", "3C", "No heat - bedroom radiator cold", "Urgent", "Open", date(2026, 9, 27), "ticket_missing"),
        ("08", "2A", "Leaking kitchen faucet", "High", "In Progress", date(2026, 9, 21), "ticket_closed"),
        ("06", "4C", "Mold spotting on bathroom ceiling", "High", "In Progress", date(2026, 9, 18), "ticket_rewritten"),
    ]:
        u = unit_of(code, label)
        tickets.append({"ticket_id": f"BV-WO-{ticket_seq}", "property_id": u["property_id"], "unit_id": u["unit_id"],
                        "summary": summary, "priority": prio, "status": status, "opened_on": opened.isoformat(),
                        "_unit": u, "_scenario": scen, "_opened": opened})
        ticket_seq += 1
    tickets.sort(key=lambda t: t["ticket_id"])
    completed = [t for t in tickets if t["status"] == "Completed"]
    unmigrated_completed = {t["ticket_id"] for t in rng.sample(completed, 6)}

    # ---------------------------------------------------------------- consolidated model
    # Cedar assigns new IDs in its own order (reverse property order) - harmless re-keying.
    t_props, t_units, t_tenants, t_leases, t_deposits, t_tickets = [], [], [], [], [], []
    prop_map, unit_map, tenant_map, lease_map = {}, {}, {}, {}
    for i, (code, name, _street, tstreet, city, *_rest) in enumerate(reversed(PROPERTIES)):
        pid = f"CPM-B{(i + 1) * 10:03d}"
        prop_map[f"BV-P-{code}"] = pid
        t_props.append({"Property Ref": pid, "Property Name": name, "Address": tstreet, "City": city,
                        "State": "CA", "Legacy Ref": f"BV-P-{code}"})
    seq = 20001
    for u in sorted(units, key=lambda u: (-int(u["_code"]), u["_label"])):
        uid = f"CPM-U-{seq}"
        seq += 1
        unit_map[u["unit_id"]] = uid
        label = f"#{u['_label'][:-1]}-{u['_label'][-1]}" if u["_code"] == "01" else u["_label"]
        t_units.append({"Unit Ref": uid, "Property Ref": prop_map[u["property_id"]], "Unit": label,
                        "Beds": u["bedrooms"]})

    seq = 10001
    style = 0
    dup_source_id = None
    for t in tenants:
        if t["tenant_id"] in {h["tenant_id"] for h in history if h["lease_id"] == unmigrated_history}:
            continue
        rid = f"CPM-R-{seq}"
        seq += 1
        tenant_map[t["tenant_id"]] = rid
        email = t["email"]
        if style % 9 == 4:
            email = email.title().replace("@Example.Com", "@Example.com")
        phone_digits = "".join(ch for ch in t["phone"] if ch.isdigit())
        t_tenants.append({"Resident ID": rid, "Resident Name": f"{t['first_name']} {t['last_name']}",
                          "Email Address": email, "Phone": tgt_phone(phone_digits[:3] + phone_digits[-4:], style)})
        style += 1
        if (t["first_name"], t["last_name"]) == ("Alex", "Kim"):
            # Cedar has two Alex Kim residents and neither carries Bayview's email.
            t_tenants[-1]["Email Address"] = "alex.kim.home@example.com"
            t_tenants[-1]["Phone"] = "415.555.0142"
            tenant_map["ALEX_B"] = rid
            rid2 = f"CPM-R-{seq}"
            seq += 1
            tenant_map["ALEX_A"] = rid2
            t_tenants.append({"Resident ID": rid2, "Resident Name": "Alex Kim", "Email Address": "akim@example.com",
                              "Phone": "(415) 555-0199"})
        if (t["first_name"], t["last_name"]) == ("Priya", "Raman"):
            dup_source_id = t["tenant_id"]
    # duplicate Priya Raman created later by a second import batch
    dup_rid = f"CPM-R-{seq}"
    seq += 1
    t_tenants.append({"Resident ID": dup_rid, "Resident Name": "Priya Raman",
                      "Email Address": "Priya.Raman@Example.com", "Phone": "(510) 555-0123"})
    rosa_rid = f"CPM-R-{seq}"
    seq += 1
    t_tenants.append({"Resident ID": rosa_rid, "Resident Name": "Rosa Lindqvist",
                      "Email Address": "rosa.lindqvist@example.com", "Phone": "650.555.0177"})

    seq = 50001
    for l in leases:
        scen = l["_scenario"]
        if scen == "missing_lease" or l["lease_id"] == unmigrated_history:
            continue
        lid = f"CPM-L-{seq}"
        seq += 1
        lease_map[l["lease_id"]] = lid
        rent, status, end = l["_rent"], ("Current" if l["status"] == "Active" else "Past"), l["_end"]
        tenant_ref = tenant_map[l["tenant_id"]]
        unit_ref = unit_map[l["unit_id"]]
        start = l["_start"]
        if scen == "rent_decrease":
            rent = 205000
        elif scen == "inactive_and_rent":
            status, rent = "Past", 157500
        elif scen == "rent_increase":
            rent = 209500
        elif scen == "term_change":
            end = date(2026, 3, 31)
        elif scen == "orphan_unit":
            unit_ref = "CPM-U-29999"
        elif scen == "wrong_unit":
            unit_ref = unit_map[unit_of("07", "1D")["unit_id"]]
        elif scen == "duplicate_tenant":
            tenant_ref = dup_rid
        elif scen == "ambiguous":
            # Lease "B": the right person (same phone), wrong unit (2D)
            tenant_ref = tenant_map["ALEX_B"]
            unit_ref = unit_map[unit_of("03", "2D")["unit_id"]]
        t_leases.append({"Lease Ref": lid, "Resident ID": tenant_ref, "Unit Ref": unit_ref,
                         "Move-In Date": us_date(start), "Lease End": us_date(end), "Lease Status": status,
                         "Base Rent": money_fmt(rent)})
        if scen == "ambiguous":
            lease_map["ALEX_B"] = lid
            lid_a = f"CPM-L-{seq}"
            seq += 1
            # Lease "A": right unit (2C), a different-looking Alex Kim, different dates and rent
            t_leases.append({"Lease Ref": lid_a, "Resident ID": tenant_map["ALEX_A"],
                             "Unit Ref": unit_map[unit_of("03", "2C")["unit_id"]],
                             "Move-In Date": us_date(date(2025, 12, 15)), "Lease End": us_date(date(2026, 12, 14)),
                             "Lease Status": "Current", "Base Rent": money_fmt(192500)})
    rosa_lid = f"CPM-L-{seq}"
    seq += 1
    t_leases.append({"Lease Ref": rosa_lid, "Resident ID": rosa_rid,
                     "Unit Ref": unit_map[unit_of("01", "5F")["unit_id"]],
                     "Move-In Date": us_date(date(2026, 10, 1)), "Lease End": us_date(date(2027, 9, 30)),
                     "Lease Status": "Current", "Base Rent": money_fmt(215000)})

    seq = 70001
    for d in deposits:
        scen = d["_lease"]["_scenario"]
        if scen in ("missing_lease", "deposit_missing"):
            continue
        amount = d["_amount"]
        if scen == "deposit_mismatch":
            amount = 156000
        ref = lease_map["ALEX_B"] if scen == "ambiguous" else lease_map[d["lease_id"]]
        t_deposits.append({"Deposit Ref": f"CPM-SD-{seq}", "Lease Ref": ref, "Deposit Held": money_fmt(amount)})
        seq += 1
    t_deposits.append({"Deposit Ref": f"CPM-SD-{seq}", "Lease Ref": rosa_lid, "Deposit Held": money_fmt(215000)})

    seq = 90001
    for tk in tickets:
        if tk["_scenario"] == "ticket_missing" or tk["ticket_id"] in unmigrated_completed:
            continue
        status = TGT_TICKET_STATUS[tk["status"]]
        if tk["_scenario"] == "ticket_closed":
            status = "Closed"
        desc = tk["summary"] if seq % 4 else tk["summary"].lower() + "."
        unit_ref, created = unit_map[tk["unit_id"]], tk["_opened"]
        if tk["_scenario"] == "ticket_rewritten":
            # re-keyed at property level with the description retyped and the date of data entry
            desc, unit_ref, created = ("Mould on bathroom ceiling above shower, tenant requests inspection", "",
                                       tk["_opened"] + timedelta(days=4))
        t_tickets.append({"Work Order #": f"CPM-WO-{seq}", "Property Ref": prop_map[tk["property_id"]],
                          "Unit Ref": unit_ref, "Description": desc,
                          "Priority": TGT_PRIORITY[tk["priority"]], "Status": status,
                          "Created": us_date(created)})
        seq += 1

    # ---------------------------------------------------------------- write
    def write(path: Path, rows: list[dict]) -> None:
        cols = [c for c in rows[0].keys() if not c.startswith("_")]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: v for k, v in r.items() if not k.startswith("_")})

    b, a = OUT / "before", OUT / "after"
    write(b / "bayview_properties.csv", props)
    write(b / "bayview_units.csv", units)
    write(b / "bayview_tenants.csv", tenants)
    write(b / "bayview_leases.csv", leases)
    write(b / "bayview_deposits.csv", deposits)
    write(b / "bayview_work_orders.csv", tickets)
    write(a / "cedar_properties.csv", t_props)
    write(a / "cedar_units.csv", t_units)
    write(a / "cedar_residents.csv", sorted(t_tenants, key=lambda r: r["Resident ID"]))
    write(a / "cedar_leases.csv", t_leases)
    write(a / "cedar_deposits.csv", t_deposits)
    write(a / "cedar_maintenance.csv", t_tickets)
    # A consolidated rent roll with the kind of headers people really export — for trying AI column suggestions.
    messy_names = {"Lease Ref": "Agreement No", "Resident ID": "Occupant", "Unit Ref": "Apartment Ref",
                   "Move-In Date": "Lease Commencement", "Lease End": "Lease Expires On",
                   "Lease Status": "Occupancy State", "Base Rent": "Monthly Charge Amt"}
    write(OUT / "messy" / "cedar_rent_roll_messy_headers.csv",
          [{messy_names[k]: v for k, v in r.items()} for r in t_leases])

    active = [l for l in leases if l["status"] == "Active"]
    print(f"properties={len(props)} units={len(units)} tenants={len(tenants)} leases={len(leases)} "
          f"active={len(active)} deposits={len(deposits)} tickets={len(tickets)} "
          f"open_tickets={sum(1 for t in tickets if t['status'] != 'Completed')}")
    print(f"target: tenants={len(t_tenants)} leases={len(t_leases)} deposits={len(t_deposits)} tickets={len(t_tickets)}")
    print(f"unmigrated history lease={unmigrated_history} dup source tenant={dup_source_id}")


if __name__ == "__main__":
    main()
