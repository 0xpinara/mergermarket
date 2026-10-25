"""A lived-in demo portfolio for a visitor's first visit, so no page opens empty.

Everything here is fictional: companies, people, addresses, amounts, e-mails (example.com) and phone numbers
(555-01xx). Nothing is mocked inside the product. Every audit, plan, dry run, sign-off, receipt and review below is
produced by the real engine and the real API handlers, replayed along a plausible timeline (their timestamps
are set to that timeline rather than "just now").

  A · Cedar × Bayview — the bundled sample. Planned, dry-run caught the unit-key mistake, fixed, signed off,
      loaded at cutover, verified; reviewers are working through the import-time findings.
  B · Cedar × Lakeshore Living — an earlier migration. Its consolidated export comes from the evaluation
      harness's seeded perturbation (re-keyed, reformatted, ~20 injected defects); fully reviewed and released
      with a receipt.
  C · Cedar × Pinecrest Rentals — an upcoming deal: legacy exports only, mapping plan half reviewed.
  Plus a Deal Intelligence pipeline of fictional prospects.
"""
from __future__ import annotations

import csv
import io
import random
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone

from . import db, ingest, services
from .engine import metrics, runner
from .integrate import dryrun, export, readiness
from .integrate import plan as planmod

_LOCK = threading.Lock()
TEMPLATE = "cedar-import-v1"
LEAD, OPS, FIN, MAINT = "Ana Reyes", "Marcus Bell", "Jordan Ellis", "Sam Okoro"


# ----------------------------------------------------------------------------------------------- timeline clock

class Timeline:
    """Feeds db.now() for this request only: a fixed moment that advances a little on every call, so ordering
    (dry run before sign-off before verification) is exactly what the real flow requires."""

    def __init__(self):
        self.t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.rng = random.Random(7)

    def at(self, iso: str):
        self.t = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    def __call__(self) -> str:
        self.t += timedelta(seconds=self.rng.randint(4, 40))
        return self.t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ----------------------------------------------------------------------------------------------- data helpers

def _csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _add(conn, did: str, side: str, name: str, rows: list[dict]) -> None:
    for t in ingest.parse_upload(name, _csv(rows)):
        services.add_snapshot(conn, did, side, t, origin="seed")


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


FIRST = ["Aiden", "Beatriz", "Callum", "Delphine", "Emeka", "Fiona", "Goran", "Helena", "Idris", "Juniper",
         "Kai", "Lucia", "Malik", "Noor", "Orla", "Pavel", "Rosalind", "Soren", "Tamsin", "Vikram", "Willa",
         "Xiomara", "Yannick", "Zara", "Ansel", "Bianca", "Cormac", "Dalia", "Ezra", "Frida", "Gus", "Hollis",
         "Ines", "Jasper", "Kalani", "Liesel", "Mirela", "Nils", "Odette", "Pilar", "Reza", "Saoirse", "Teodor",
         "Una", "Valentin", "Wren", "Yusra", "Zev", "Amelie", "Bastian"]
LAST = ["Achterberg", "Bramwell", "Cardenas", "Dufresne", "Eklund", "Farrow", "Gutierrez", "Holloway", "Ishikawa",
        "Jensen", "Kaminski", "Laurent", "Montero", "Nakashima", "Okonkwo", "Petrakis", "Quintana", "Rourke",
        "Sandoval", "Torvik", "Uchida", "Varga", "Whitcombe", "Yamada", "Zielinski", "Abiola", "Bergstrom",
        "Castillo", "Duval", "Engel", "Fonseca", "Gallagher", "Hendricks", "Iversen", "Jablonski", "Kovacs",
        "Lachance", "Marchetti", "Nordin", "Oduya", "Palacios", "Rinaldi", "Sutherland", "Tremblay", "Ulrich",
        "Vasquez", "Winslow", "Yildiz", "Zubiri", "Albrecht"]


def _rebrand(before: dict, code: str, props: list[tuple], seed: int, area_codes: list[str],
             keep_props: int | None = None, rent_level: float = 1.0) -> dict:
    """A new fictional portfolio with the sample's shape: new IDs, buildings, people, contact details and its own
    rent levels (each building priced around `rent_level` × the sample's, deposits moving with rent)."""
    rng = random.Random(seed)
    old_pids = [p["property_id"] for p in before["properties"]]
    keep = set(old_pids[:keep_props]) if keep_props else set(old_pids)
    factor = {pid: rent_level * rng.uniform(0.93, 1.08) for pid in old_pids}
    unit_prop = {u["unit_id"]: u["property_id"] for u in before["units"]}
    lease_prop = {l["lease_id"]: unit_prop.get(l["unit_id"]) for l in before["leases"]}

    def price(v: str, pid: str | None) -> str:
        try:
            dollars = float(v)
        except (TypeError, ValueError):
            return v
        return f"{round(dollars * factor.get(pid, rent_level) / 5) * 5:.2f}"

    def rid(v: str) -> str:
        return v.replace("BV-", f"{code}-") if v else v

    out: dict[str, list[dict]] = {}
    pmap = {}
    out["properties"] = []
    for pid, (name, street, city, state) in zip(old_pids, props):
        if pid not in keep:
            continue
        pmap[pid] = name
        out["properties"].append({"property_id": rid(pid), "property_name": name, "street_address": street,
                                  "city": city, "state": state})
    units = [u for u in before["units"] if u["property_id"] in keep]
    unit_ids = {u["unit_id"] for u in units}
    out["units"] = []
    for u in units:
        initials = "".join(w[0] for w in pmap[u["property_id"]].split()[:2]).upper()
        label = re.sub(r"^Apt\s+", "", u["unit_number"])
        out["units"].append({**u, "unit_id": rid(u["unit_id"]), "property_id": rid(u["property_id"]),
                             "unit_code": f"{initials}-{label}"})
    leases = [l for l in before["leases"] if l["unit_id"] in unit_ids]
    tenant_ids = {l["tenant_id"] for l in leases}
    names = rng.sample([(f, la) for f in FIRST for la in LAST], len(tenant_ids))
    phones = rng.sample([f"({a}) 555-01{n:02d}" for a in area_codes for n in range(100)], len(tenant_ids))
    out["tenants"] = []
    for t, (f, la), ph in zip(sorted((t for t in before["tenants"] if t["tenant_id"] in tenant_ids),
                                     key=lambda t: t["tenant_id"]), names, phones):
        out["tenants"].append({"tenant_id": rid(t["tenant_id"]), "first_name": f, "last_name": la,
                               "email": f"{_ascii(f).lower()}.{_ascii(la).lower()}@example.com", "phone": ph})
    out["leases"] = [{**l, "lease_id": rid(l["lease_id"]), "tenant_id": rid(l["tenant_id"]),
                      "unit_id": rid(l["unit_id"]), "monthly_rent": price(l["monthly_rent"], unit_prop[l["unit_id"]])}
                     for l in leases]
    lease_ids = {l["lease_id"] for l in leases}
    out["deposits"] = [{**d, "deposit_id": rid(d["deposit_id"]), "lease_id": rid(d["lease_id"]),
                        "tenant_id": rid(d["tenant_id"]),
                        "deposit_balance": price(d["deposit_balance"], lease_prop.get(d["lease_id"]))}
                       for d in before["deposits"] if d["lease_id"] in lease_ids]
    out["maintenance"] = [{**w, "ticket_id": rid(w["ticket_id"]), "property_id": rid(w["property_id"]),
                           "unit_id": rid(w["unit_id"])} for w in before["maintenance"] if w["property_id"] in keep]
    return out


def _cedar_keys(after: dict, seed: int, series: str) -> dict:
    """The harness writes eval-style keys; give the consolidated export Cedar's own key style instead."""
    prefix = {"P": f"CPM-B{series}", "U": f"CPM-U-{series}", "T": f"CPM-R-{series}", "L": f"CPM-L-{series}",
              "D": f"CPM-SD-{series}", "W": f"CPM-WO-{series}"}
    pat = re.compile(rf"EV-([PUTLDW]){seed}-")

    def fix(v):
        if not isinstance(v, str):
            return v
        v = pat.sub(lambda m: prefix[m.group(1)], v)
        return re.sub(r"-DUP$", "B", v)

    return {ent: [{k: fix(v) for k, v in r.items()} for r in rows] for ent, rows in after.items()}


# ----------------------------------------------------------------------------------------------- review helpers

NOTES = {
    "L-001": ("resolve", FIN, "Lease recreated in Cedar from the executed lease PDF; billing resumes next cycle."),
    "L-002": ("resolve", FIN, "Rent corrected to the legacy amount; a credit memo covers the short-billed month."),
    "L-003": ("expected_exception", OPS, "Renewal at the higher rent was signed before cutover; legacy export "
                                         "predates it."),
    "L-004": ("resolve", OPS, "Lease reactivated in Cedar — the status was mis-mapped in the import batch."),
    "L-005": ("resolve", OPS, "Dates corrected to match the executed lease."),
    "L-007": ("resolve", OPS, "Lease moved to the correct unit in Cedar."),
    "L-008": ("resolve", OPS, "Resident reassigned on the lease."),
    "L-009": ("resolve", OPS, "Unit reference repaired; lease re-linked to its unit."),
    "L-010": ("resolve", OPS, "Resident reference repaired."),
    "L-011": ("expected_exception", OPS, "New lease signed after cutover — no legacy record expected."),
    "L-012": ("expected_exception", LEAD, "Ended-lease history archived separately per the migration plan."),
    "L-013": ("resolve", FIN, "Duplicate lease voided in Cedar before the first billing run."),
    "L-014": ("resolve", OPS, "Status set back to Past."),
    "L-015": ("resolve", OPS, "Signed future lease added to Cedar."),
    "T-001": ("resolve", MAINT, "Duplicate resident merged; lease and deposit now sit on one record."),
    "D-001": ("resolve", FIN, "Deposit recorded against the lease; matches the trust-account statement."),
    "D-002": ("resolve", FIN, "Balance corrected to the trust-account ledger."),
    "D-003": ("expected_exception", FIN, "Deposit received after cutover — receipt on file."),
    "D-004": ("resolve", FIN, "Deposit re-linked to its lease."),
    "D-005": ("resolve", FIN, "Held amount confirmed with the trust account and entered."),
    "M-001": ("resolve", MAINT, "Work order recreated in Cedar and re-dispatched to the vendor."),
    "M-002": ("resolve", MAINT, "Reopened in Cedar; vendor visit rescheduled."),
    "M-003": ("resolve", MAINT, "Priority restored."),
    "M-004": ("expected_exception", MAINT, "Completed work-order history kept in the legacy archive."),
}


def _review(fid: str, action: str, reviewer: str, note: str | None = None, candidate: str | None = None,
            ws: str = "") -> bool:
    from fastapi import HTTPException

    from .main import ReviewIn
    from .main import review as review_route
    try:
        review_route(fid, ReviewIn(action=action, reviewer=reviewer, note=note, candidate=candidate), ws)
        return True
    except HTTPException:
        return False


def _close_everything(conn_ws: str, did: str, clock: Timeline, start: str) -> None:
    with db.connect() as conn:
        fs = metrics.load_findings(conn, did)
    clock.at(start)
    for f in sorted(fs, key=metrics._sev_key):
        if not metrics.is_open(f):
            continue
        clock.t += timedelta(minutes=random.Random(f["id"]).randint(25, 260))
        cands = f["payload"].get("candidates") or []
        free = [c for c in cands if not c.get("taken_by")]
        if f["confidence"] == "needs_review" and free:
            _review(f["id"], "confirm_match", OPS, "Matched against the signed lease and the resident's ID.",
                    free[0]["target_key"], ws=conn_ws)
            continue
        action, who, note = NOTES.get(f["rule_id"], ("expected_exception", LEAD,
                                                     "Reviewed with the integration team; documented."))
        _review(f["id"], action, who, note, ws=conn_ws)


def _decide_plan(conn, p: dict, financial_by: str = FIN) -> None:
    planmod.bulk_approve(conn, p, LEAD)
    for it in p["items"]:
        if it["status"] in ("approved", "ignored"):
            continue
        note = None
        if it["id"] == "leases.rent_frequency":
            note = "All legacy leases bill monthly; confirmed with the Cedar billing team."
        elif it["confidence"] in ("conflict", "weak"):
            note = "Checked against the reference evidence."
        planmod.act(conn, p, it["id"], "approve", financial_by if it["financial"] else LEAD, note)


def _status(conn, deal: dict) -> dict:
    from .main import deal_status
    return deal_status(conn, deal)


def _deal(conn, did: str) -> dict:
    return dict(conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())


# ----------------------------------------------------------------------------------------------- the portfolio

def _bayview(ws: str, clock: Timeline) -> str:
    clock.at("2026-09-22T15:08:00")
    with db.connect() as conn:
        did = services.load_sample(conn, ws)
        conn.execute("UPDATE snapshots SET created_at=?, updated_at=? WHERE deal_id=? AND side='after'",
                     ("2026-10-02T10:41:12.000Z", "2026-10-02T10:41:12.000Z", did))
        deal = _deal(conn, did)
        clock.at("2026-09-23T09:20:00")
        p = planmod.create(conn, deal, TEMPLATE, LEAD)
        clock.at("2026-09-23T09:44:00")
        planmod.act(conn, p, "units.unit_code", "edit", LEAD, "Template calls the key unit_code.", ["unit_code"])
        clock.at("2026-09-23T10:05:00")
        _decide_plan(conn, p)
        clock.at("2026-09-24T08:52:00")
        dryrun.run(conn, deal, p, LEAD)
        clock.at("2026-09-24T09:31:00")
        planmod.act(conn, p, "units.unit_code", "edit", LEAD,
                    "Leases and work orders reference unit_id; unit_code is the door code.", ["unit_id"])
        clock.at("2026-09-24T09:36:00")
        dryrun.run(conn, deal, p, LEAD)
        clock.at("2026-09-26T16:20:00")
        readiness.sign(conn, deal, LEAD, True, _status(conn, deal))
        clock.at("2026-09-26T16:27:00")
        export.build_zip(conn, deal)
        clock.at("2026-10-02T11:02:00")
        runner.run_audit(conn, deal)
        fs = metrics.load_findings(conn, did)

    def find(rule: str, who: str) -> str | None:
        return next((f["id"] for f in fs if f["rule_id"] == rule and who in f["subject"]), None)

    script = [
        ("2026-10-02T15:40:00", "L-001", "Dana Whitfield", "comment", LEAD,
         "Escalated to the Cedar data team: lease must be recreated before November billing."),
        ("2026-10-02T15:52:00", "L-002", "Theo Alvarez", "investigate", FIN,
         "Pulled the Cedar rent schedule — 5A shows $2,050 from the import batch. Waiting on Bayview's signed "
         "renewal before correcting."),
        ("2026-10-03T09:15:00", "D-002", "Ines Duarte", "resolve", FIN,
         "Trust-account statement shows $1,650.00 held. Corrected in Cedar."),
        ("2026-10-03T10:02:00", "T-001", "Priya Raman", "resolve", MAINT,
         "Merged the duplicate resident record in Cedar; lease and deposit now on one resident."),
        ("2026-10-03T11:30:00", "L-003", "Lena Marsh", "expected_exception", OPS,
         "Renewal at $2,095 was signed 2026-09-12 and keyed in Cedar directly; Bayview's export predates it."),
        ("2026-10-05T14:10:00", "M-002", "Leaking kitchen faucet", "resolve", MAINT,
         "Reopened in Cedar; plumber rescheduled for 10/09."),
        ("2026-10-06T09:05:00", "L-011", "Rosa Lindqvist", "expected_exception", OPS,
         "New lease signed 2026-10-01, after cutover — no legacy record expected."),
        ("2026-10-06T09:12:00", "L-012", "Clara Lombardi", "expected_exception", LEAD,
         "Ended-lease history archived separately per the migration plan."),
        ("2026-10-06T09:20:00", "M-004", "completed work orders", "expected_exception", MAINT,
         "Completed work-order history kept in the Bayview archive."),
        ("2026-10-14T13:45:00", "D-001", "Mina Okafor", "investigate", FIN,
         "Confirming the $1,800 against the trust account before recording it."),
        ("2026-10-21T10:30:00", "L-002", "Omar Haddad", "investigate", OPS,
         "Cedar shows 3D as Past at $1,575. Asked the property manager whether the tenant gave notice."),
    ]
    for at, rule, who, action, reviewer, note in script:
        fid = find(rule, who)
        if fid:
            clock.at(at)
            _review(fid, action, reviewer, note, ws=ws)
    return did


def _lakeshore(ws: str, clock: Timeline) -> str:
    from eval.harness import load_before, perturb

    seed = 41
    props = [("Lakeshore Commons", "410 Lakeshore Drive", "Lake Arden", "WA"),  # 7 of these are kept
             ("Birchwood Court", "77 Birchwood Lane", "Lake Arden", "WA"),
             ("Harbor Light Apartments", "1200 Harbor Light Way", "Lake Arden", "WA"),
             ("Osprey Point", "35 Osprey Point Road", "Lake Arden", "WA"),
             ("Fern Hill Terrace", "618 Fern Hill Terrace", "North Arden", "WA"),
             ("Maple Crossing", "92 Maple Crossing", "North Arden", "WA"),
             ("Stillwater Flats", "2400 Stillwater Avenue", "North Arden", "WA"),
             ("Kestrel Row", "14 Kestrel Row", "North Arden", "WA")]
    legacy = _rebrand(load_before(), "LK", props, seed=1101, area_codes=["206", "253", "425", "360"],
                      keep_props=7, rent_level=1.11)
    after, _truth = perturb(seed, legacy)
    after = _cedar_keys(after, seed, "6")
    files = {"properties": "properties", "units": "units", "tenants": "residents", "leases": "leases",
             "deposits": "deposits", "maintenance": "work_orders"}
    clock.at("2026-08-18T13:30:00")
    with db.connect() as conn:
        did = services.create_deal(conn, ws, {
            "title": "Cedar × Lakeshore Living | Portfolio Consolidation", "industry": "Property management · multifamily",
            "acquirer": "Cedar Property Management", "target_business": "Lakeshore Living Co.", "deal_date": "2026-08-31",
            "old_system": "Lakeshore legacy PMS", "new_system": "Cedar consolidated platform",
            "source_notes": ("Legacy export taken at the 2026-08-31 cutover; consolidated export pulled 2026-09-02. "
                             "Fictional companies, people and amounts — synthetic demonstration data generated by "
                             "MergeProof's evaluation harness (seeded, re-keyed, with injected defects)."),
            "source_ref": "Synthetic demo portfolio (harness seed 41)",
            "materiality_rent_cents": 50000, "materiality_deposit_cents": 100000}, is_sample=True)
        for ent, rows in legacy.items():
            _add(conn, did, "before", f"lakeshore_{files[ent]}.csv", rows)
        clock.at("2026-09-02T09:10:00")
        for ent, rows in after.items():
            _add(conn, did, "after", f"cedar_lakeshore_{files[ent]}.csv", rows)
        deal = _deal(conn, did)
        clock.at("2026-08-19T10:00:00")
        p = planmod.create(conn, deal, TEMPLATE, LEAD)
        clock.at("2026-08-19T10:30:00")
        _decide_plan(conn, p)
        clock.at("2026-08-20T14:00:00")
        dryrun.run(conn, deal, p, LEAD)
        clock.at("2026-08-24T11:15:00")
        readiness.sign(conn, deal, LEAD, True, _status(conn, deal))
        export.build_zip(conn, deal)
        clock.at("2026-09-02T09:30:00")
        runner.run_audit(conn, deal)
    _close_everything(ws, did, clock, "2026-09-02T13:00:00")
    # Confirmed matches change the inputs, so verification is rerun (as the app asks) and anything new reviewed.
    clock.at("2026-09-10T08:30:00")
    with db.connect() as conn:
        runner.run_audit(conn, _deal(conn, did))
    _close_everything(ws, did, clock, "2026-09-10T09:15:00")
    clock.at("2026-09-12T16:45:00")
    with db.connect() as conn:
        deal = _deal(conn, did)
        if readiness.evaluate(conn, deal, _status(conn, deal))["release_ready"]:
            readiness.build_receipt(conn, deal, LEAD, _status(conn, deal))
    return did


def _pinecrest(ws: str, clock: Timeline) -> str:
    from eval.harness import load_before

    props = [("Pinecrest Villas", "500 Pinecrest Boulevard", "Pine Hollow", "OR"),
             ("Summit Ridge", "1830 Summit Ridge Road", "Pine Hollow", "OR"),
             ("Aspen Glen", "71 Aspen Glen Court", "Pine Hollow", "OR")]
    legacy = _rebrand(load_before(), "PC", props + [("", "", "", "")] * 5, seed=2203, area_codes=["503", "541"],
                      keep_props=3, rent_level=0.86)
    files = {"properties": "properties", "units": "units", "tenants": "tenants", "leases": "rent_roll",
             "deposits": "deposits", "maintenance": "work_orders"}
    clock.at("2026-10-20T16:05:00")
    with db.connect() as conn:
        did = services.create_deal(conn, ws, {
            "title": "Cedar × Pinecrest Rentals | Rent Roll Import", "industry": "Property management · small multifamily",
            "acquirer": "Cedar Property Management", "target_business": "Pinecrest Rentals LLC", "deal_date": "2026-11-15",
            "old_system": "Pinecrest spreadsheets + legacy PMS", "new_system": "Cedar consolidated platform",
            "source_notes": ("Pre-close: legacy exports received 2026-10-20 for planning; cutover scheduled "
                             "2026-11-15. Fictional companies, people and amounts — synthetic demonstration data."),
            "source_ref": "Synthetic demo portfolio",
            "materiality_rent_cents": 50000, "materiality_deposit_cents": 100000}, is_sample=True)
        for ent, rows in legacy.items():
            _add(conn, did, "before", f"pinecrest_{files[ent]}.csv", rows)
        deal = _deal(conn, did)
        clock.at("2026-10-21T09:40:00")
        p = planmod.create(conn, deal, TEMPLATE, LEAD)
        clock.at("2026-10-22T14:05:00")
        planmod.bulk_approve(conn, p, LEAD)
        planmod.act(conn, p, "leases.base_rent", "approve", FIN)
    return did


PROSPECTS = [
    {"company_name": "Northgate Residential Group", "sector": "Multifamily property management", "location":
     "Spokane, WA", "event_date": "2026-09-08", "pilot_status": "interviewing", "contact_role": "VP, Operations",
     "notes": "Announced the purchase of a 410-unit, 9-building portfolio; consolidating onto their main PMS in "
              "Q1 2027. Interview #2 booked — wants a dry run on two buildings before signing.",
     "website": "https://northgate-residential.example", "source_link": "https://example.com/news/northgate-portfolio"},
    {"company_name": "Lakeshore Living Co.", "sector": "Multifamily property management", "location": "Lake Arden, WA",
     "event_date": "2026-08-31", "pilot_status": "pilot_active", "contact_role": "Integration lead (at Cedar)",
     "notes": "Pilot: Cedar used MergeProof for the Lakeshore consolidation. Released with a receipt on 9/12; asking "
              "for monthly re-verification during the first two billing cycles.",
     "website": "https://lakeshore-living.example", "source_link": "https://example.com/news/cedar-lakeshore",
     "link": "lakeshore"},
    {"company_name": "Pinecrest Rentals LLC", "sector": "Small multifamily", "location": "Pine Hollow, OR",
     "event_date": "2026-10-14", "pilot_status": "pilot_proposed", "contact_role": "Owner / operator",
     "notes": "Selling 64 units to Cedar; cutover 11/15. Their rent roll lives partly in spreadsheets — good test "
              "of the mapping planner. Proposal sent 10/22.",
     "website": "https://pinecrest-rentals.example", "source_link": "https://example.com/news/pinecrest-sale",
     "link": "pinecrest"},
    {"company_name": "Bayview Rentals", "sector": "Small multifamily", "location": "Alder Bay, CA",
     "event_date": "2026-09-30", "pilot_status": "pilot_active", "contact_role": "Controller",
     "notes": "Acquired by Cedar; 187 leases migrated at the 9/30 cutover. Verification found 19 import-time "
              "findings; reviewers closing them out before November billing.",
     "website": "https://bayview-rentals.example", "source_link": "https://example.com/news/cedar-bayview",
     "link": "bayview"},
    {"company_name": "Ironwood Housing Partners", "sector": "Affordable housing operator", "location": "Tacoma, WA",
     "event_date": "2026-07-22", "pilot_status": "contacted", "contact_role": "Director of Compliance",
     "notes": "Merging two regional operators (~1,100 units). Compliance cares most about deposit liabilities and "
              "audit trail — lead with D-001/D-005 and the receipt.",
     "website": "https://ironwood-housing.example", "source_link": "https://example.com/news/ironwood-merger"},
    {"company_name": "Saltmarsh Property Co.", "sector": "Student housing", "location": "Eugene, OR",
     "event_date": "2026-10-03", "pilot_status": "researching", "contact_role": "",
     "notes": "Switching software vendors ahead of the spring term; 3 buildings, heavy lease turnover in June. "
              "Find the right contact.",
     "website": "https://saltmarsh-property.example", "source_link": "https://example.com/news/saltmarsh-software"},
    {"company_name": "Redfern & Hale Management", "sector": "Mixed-use / retail", "location": "Boise, ID",
     "event_date": "2026-06-11", "pilot_status": "not_a_fit", "contact_role": "COO",
     "notes": "Mostly commercial leases with CAM reconciliations — outside today's residential rules. Revisit when "
              "commercial lease rules exist.",
     "website": "https://redfern-hale.example", "source_link": "https://example.com/news/redfern-hale"},
]


def _intel(ws: str, clock: Timeline, links: dict[str, str]) -> None:
    from .main import IntelIn, intel_create
    for i, p in enumerate(PROSPECTS):
        clock.at((datetime(2026, 9, 1) + timedelta(days=i * 6, hours=i)).isoformat())
        body = {k: v for k, v in p.items() if k != "link"}
        body["linked_deal_id"] = links.get(p.get("link", ""))
        intel_create(IntelIn(**body), ws)


def seed(ws: str) -> dict:
    """Seed the workspace once. Returns {"seeded": bool, "deal_id": primary deal or None}."""
    with _LOCK:
        with db.connect() as conn:
            done = conn.execute("SELECT 1 FROM demo_seeds WHERE workspace=?", (ws,)).fetchone()
            has = conn.execute("SELECT id FROM deals WHERE workspace=? ORDER BY updated_at DESC LIMIT 1",
                               (ws,)).fetchone()
        if done or has:
            return {"seeded": False, "deal_id": has["id"] if has else None}
        clock = Timeline()
        token = db.CLOCK.set(clock)
        try:
            bay = _bayview(ws, clock)
            lake = _lakeshore(ws, clock)
            pine = _pinecrest(ws, clock)
            _intel(ws, clock, {"bayview": bay, "lakeshore": lake, "pinecrest": pine})
        finally:
            db.CLOCK.reset(token)
        with db.connect() as conn:
            conn.execute("INSERT INTO demo_seeds (workspace, seeded_at) VALUES (?, ?)", (ws, db.now()))
            # Most recently touched first in the deal switcher: Bayview, then Pinecrest, then Lakeshore.
            for did, ts in ((bay, "2026-10-24T17:12:00.000Z"), (pine, "2026-10-22T14:06:00.000Z"),
                            (lake, "2026-09-12T16:47:00.000Z")):
                conn.execute("UPDATE deals SET updated_at=? WHERE id=?", (ts, did))
        return {"seeded": True, "deal_id": bay}
