"""Plan → dry run → sign-off → export → verify → receipt, end to end on the synthetic sample."""
import hashlib
import io
import json
import zipfile

import pytest

from app import config, db
from app.integrate import template as tpl
from app.integrate import transforms

from .conftest import WS

OTHER = {"X-Workspace": "ws_other_000002"}


def _sample(client) -> str:
    return client.post("/api/sample", headers=WS, json={}).json()["deal"]["id"]


def _base(did):
    return f"/api/deals/{did}/integrate"


def _plan(client, did) -> dict:
    r = client.post(_base(did) + "/plan", headers=WS, json={"template_id": "cedar-import-v1", "reviewer": "Ana"})
    assert r.status_code == 201, r.text
    return r.json()


def _item(plan, iid):
    return next(i for i in plan["items"] if i["id"] == iid)


def _act(client, did, iid, action, **kw):
    return client.post(_base(did) + f"/plan/items/{iid}", headers=WS, json={"action": action, "reviewer": "Ana", **kw})


def _decide_all(client, did) -> dict:
    """What a careful reviewer does: bulk-approve the exact non-financial columns, then each of the rest by hand."""
    r = client.post(_base(did) + "/plan/bulk-approve", headers=WS, json={"reviewer": "Ana"})
    assert r.status_code == 200
    plan = r.json()["plan"]
    for it in plan["items"]:
        if it["status"] in ("approved", "ignored"):
            continue
        note = "Checked against the reference evidence." if it["needs_note"] else None
        r = _act(client, did, it["id"], "approve", note=note)
        assert r.status_code == 200, (it["id"], r.text)
        plan = r.json()
    return plan


def _dry(client, did) -> dict:
    r = client.post(_base(did) + "/dry-run", headers=WS, json={"reviewer": "Ana"})
    assert r.status_code == 200, r.text
    return r.json()


def _gates(client, did) -> dict:
    return {g["id"]: g["status"] for g in client.get(_base(did) + "/readiness", headers=WS).json()["gates"]}


# --------------------------------------------------------------------------- template + transforms

def test_bundled_template_is_valid_and_strict():
    t = tpl.get_bundled("cedar-import-v1")
    assert [x["name"] for x in t["tables"]][:2] == ["properties", "units"]
    bad = json.loads((config.SAMPLE_DIR / "target_template" / "cedar_import_v1.json").read_text())
    bad["tables"][1]["columns"][1]["ref"] = "work_orders"   # forward reference
    with pytest.raises(tpl.TemplateError, match="earlier table"):
        tpl.validate(bad)
    bad = json.loads((config.SAMPLE_DIR / "target_template" / "cedar_import_v1.json").read_text())
    bad["tables"][0]["columns"][0]["type"] = "python"
    with pytest.raises(tpl.TemplateError, match="unknown type"):
        tpl.validate(bad)


def test_transform_allowlist_rejects_anything_else():
    t = tpl.get_bundled("cedar-import-v1")
    leases = next(x for x in t["tables"] if x["name"] == "leases")
    rent = next(c for c in leases["columns"] if c["name"] == "base_rent")
    status = next(c for c in leases["columns"] if c["name"] == "lease_status")
    with pytest.raises(transforms.TransformError):
        transforms.validate(rent, leases, {"op": "eval", "expr": "x*1.1"}, ["monthly_rent"], ["monthly_rent"])
    with pytest.raises(transforms.TransformError):  # money can never be a constant
        transforms.validate(rent, leases, {"op": "constant", "value": "0"}, [], ["monthly_rent"])
    with pytest.raises(transforms.TransformError):
        transforms.validate(status, leases, {"op": "enum_map", "vocabulary": "lease_status",
                                             "values": {"active": "Paid in full"}}, ["status"], ["status"])
    assert transforms.apply_value({"op": "money"}, [" $2,400.5 "]) == ("2400.50", None)
    assert transforms.apply_value({"op": "money"}, [""]) == (None, None)  # blank stays blank, never 0


# --------------------------------------------------------------------------- profile + suggestions

def test_profile_finds_keys_references_and_masks_people(client):
    did = _sample(client)
    p = client.get(_base(did) + "/profile", headers=WS).json()
    units = next(f for f in p["files"] if f["entity"] == "units")
    assert set(units["keys"]) >= {"unit_id", "unit_code"}
    rel = {(r["from"]["file"], r["from"]["column"], r["to"]["column"]): r["inclusion"] for r in p["relationships"]}
    assert rel[("bayview_leases.csv", "unit_id", "unit_id")] == 1.0
    assert ("bayview_leases.csv", "unit_id", "unit_code") not in rel
    tenants = next(f for f in p["files"] if f["entity"] == "tenants")
    for c in tenants["columns"]:
        if c["column"] in ("first_name", "last_name", "email", "phone"):
            assert c["pii"] and "examples" not in c
    assert "@example.com" not in json.dumps(p)


def test_unit_key_conflict_is_routed_to_review(client):
    did = _sample(client)
    plan = _plan(client, did)
    unit_key = _item(plan, "units.unit_code")
    assert unit_key["status"] == "needs_review" and unit_key["sources"] == ["unit_id"]
    assert "unit_code" in unit_key["reasons"][0] and "100%" in unit_key["reasons"][0]
    assert [c["sources"] for c in unit_key["candidates"][:2]] == [["unit_id"], ["unit_code"]]
    for fin in ("leases.base_rent", "leases.lease_status", "security_deposits.deposit_held"):
        assert _item(plan, fin)["financial"] and not _item(plan, fin)["bulk_eligible"]
    assert _item(plan, "leases.rent_frequency")["status"] == "needs_review"
    assert _item(plan, "residents.full_name")["transform"] == {"op": "concat", "sep": " "}
    # bulk approval never touches financial or contested columns
    done = client.post(_base(did) + "/plan/bulk-approve", headers=WS, json={"reviewer": "Ana"}).json()["approved"]
    assert "units.unit_code" not in done and "leases.base_rent" not in done and len(done) >= 20


def test_review_actions_are_guarded_and_logged(client):
    did = _sample(client)
    plan = _plan(client, did)
    h0 = plan["plan_hash"]
    assert _act(client, did, "units.unit_code", "approve").status_code == 422          # contested: needs a note
    r = _act(client, did, "units.bedrooms", "edit", sources=["unit_code"], note="x")
    assert r.status_code == 422 and "holds id values" in r.json()["detail"]   # type guard
    assert _act(client, did, "leases.base_rent", "edit", sources=["monthly_rent"],
                transform={"op": "eval"}).status_code == 422
    assert _act(client, did, "leases.base_rent", "edit", sources=["nope"], note="x").status_code == 422
    assert _act(client, did, "leases.base_rent", "ignore", note="later").status_code == 422   # required
    assert _act(client, did, "units.bedrooms", "ignore").status_code == 422                  # reason required
    r = _act(client, did, "units.bedrooms", "approve")
    assert r.status_code == 200 and r.json()["plan_hash"] == h0      # approval alone doesn't change the plan hash
    assert _act(client, did, "units.bedrooms", "approve").status_code == 409
    assert _act(client, did, "units.bedrooms", "reject").status_code == 409
    # Changing a decision (edit / ignore) is allowed and logged with before/after; it changes what the plan does.
    r = _act(client, did, "units.bedrooms", "ignore", note="Cedar fills bedrooms from its own inventory survey.")
    assert r.status_code == 200 and r.json()["plan_hash"] != h0 and r.json()["version"] == plan["version"] + 1
    r = _act(client, did, "units.bedrooms", "reopen")
    assert r.status_code == 200 and r.json()["plan_hash"] == h0      # content-addressed: same mapping, same hash
    assert r.json()["version"] == plan["version"] + 2 and _item(r.json(), "units.bedrooms")["status"] == "suggested"
    ev = client.get(_base(did) + "/events", headers=WS).json()
    assert [e["action"] for e in ev[:4]] == ["reopen", "ignore", "approve", "created"]
    assert ev[1]["note"].startswith("Cedar fills") and ev[1]["before"]["status"] == "approved"
    assert ev[1]["after"]["status"] == "ignored"


# --------------------------------------------------------------------------- the demo: wrong key, fix, verify

def test_wrong_unit_key_orphans_leases_then_fix_clears_it(client):
    did = _sample(client)
    _plan(client, did)
    r = _act(client, did, "units.unit_code", "edit", sources=["unit_code"])
    assert r.status_code == 422  # choosing against the evidence needs a reason
    r = _act(client, did, "units.unit_code", "edit", sources=["unit_code"],
             note="Template calls the key unit_code, so use Bayview's unit_code.")
    assert r.status_code == 200
    warn = _item(r.json(), "leases.unit_code")["live_warnings"]
    assert warn and "0/175" in warn[0]
    _decide_all(client, did)
    bad = _dry(client, did)
    assert bad["banner"] == "SIMULATED OUTPUT — NOT IMPORTED, NOT VERIFIED"
    pf = {(g["check"], g["table"], g["column"]): g["rows"] for g in bad["preflight"]}
    assert pf[("PF-REF", "leases", "unit_code")] == 187
    assert pf[("PF-CASCADE", "security_deposits", "lease_code")] == 160
    assert pf[("PF-REF", "work_orders", "unit_code")] == 71
    blast = bad["blast_radius"][0]
    assert (blast["table"], blast["column"], blast["active_leases"]) == ("leases", "unit_code", 175)
    assert blast["rent_cents"] == 38711500 and blast["deposit_cents"] == 31997500
    assert bad["predicted_kpis"]["critical"] >= 175
    g = _gates(client, did)
    assert g["G4"] == "pass" and g["G5"] == "fail" and g["G6"] == "fail" and g["G7"] == "fail"
    assert client.post(_base(did) + "/signoff", headers=WS,
                       json={"reviewer": "Ana", "acknowledge": True}).status_code == 409
    assert client.get(_base(did) + "/export.zip", headers=WS).status_code == 409

    # Fix: back to the key the other files actually reference.
    r = _act(client, did, "units.unit_code", "edit", sources=["unit_id"],
             note="Leases and work orders reference unit_id; unit_code is the door code.")
    assert r.status_code == 200 and not _item(r.json(), "leases.unit_code")["live_warnings"]
    good = _dry(client, did)
    assert good["totals"]["rejected"] == 0 and good["predicted_kpis"]["findings"] == 0
    assert all(t["status"] == "pass" for t in good["tieouts"])
    assert all(p["ties"] for p in good["properties"])
    assert good["diff"]["rejected_rows"] == {"before": 418, "after": 0}
    assert "PF-REF|leases|unit_code" in good["diff"]["preflight"]["resolved"]
    assert good["diff"]["predicted"]["resolved"] == bad["predicted_kpis"]["findings"]


def _close_findings(client, did):
    rows = client.get(f"/api/deals/{did}/findings", headers=WS).json()
    for f in rows:
        if f["status"] == "open" and (f["severity"] in ("critical", "high") or f["rent_risk_cents"]
                                      or f["deposit_cents"]):
            r = client.post(f"/api/findings/{f['id']}/review", headers=WS,
                            json={"action": "expected_exception", "reviewer": "Ana",
                                  "note": "Corrected in Cedar after the load; ticket MP-12."})
            assert r.status_code == 200, r.text


def test_signoff_export_verify_receipt(client):
    did = _sample(client)
    _plan(client, did)
    _decide_all(client, did)
    dr = _dry(client, did)
    g = _gates(client, did)
    assert all(g[f"G{i}"] == "pass" for i in range(1, 8)) and g["G8"] == "fail" and g["G9"] == "pending"
    assert client.post(_base(did) + "/signoff", headers=WS,
                       json={"reviewer": "Ana", "acknowledge": False}).status_code == 422
    r = client.post(_base(did) + "/signoff", headers=WS, json={"reviewer": "Ana", "acknowledge": True})
    assert r.status_code == 200 and r.json()["export_ready"]
    assert "Single-reviewer sign-off" in r.json()["signoff"]["statement"]

    z = client.get(_base(did) + "/export.zip", headers=WS)
    assert z.status_code == 200 and z.headers["x-package-sha256"] == hashlib.sha256(z.content).hexdigest()
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = zf.namelist()
    assert "mergeproof_import/import/cedar_import_leases.csv" in names and "mergeproof_import/lineage.csv" in names
    man = json.loads(zf.read("mergeproof_import/manifest.json"))
    assert man["status"] == "SIMULATED OUTPUT — NOT IMPORTED, NOT VERIFIED" and man["dry_run_id"] == dr["id"]
    for f in man["files"]:
        assert hashlib.sha256(zf.read("mergeproof_import/" + f["path"])).hexdigest() == f["sha256"]
    leases = zf.read("mergeproof_import/import/cedar_import_leases.csv").decode().splitlines()
    assert leases[0] == "lease_code,resident_id,unit_code,move_in_date,lease_end,lease_status,base_rent,rent_frequency"
    assert leases[1] == "CL-000001,CR-000001,CU-000001,01/01/2025,12/31/2026,Current,2400.00,Monthly"
    assert client.get(_base(did) + "/export.zip", headers=WS).content == z.content  # deterministic

    # Post-import verification: the actual Cedar export carries defects the plan could not have predicted.
    assert client.post(f"/api/deals/{did}/audit", headers=WS).status_code == 200
    v = client.get(_base(did) + "/verify", headers=WS).json()
    cmp = v["comparison"]
    assert cmp["counts"]["predicted"] == 0 and cmp["counts"]["observed_only"] == cmp["counts"]["observed"] > 0
    money = {(f["rule_id"], f["rent_risk_cents"], f["deposit_cents"]) for f in cmp["observed_only"]}
    assert {("L-001", 225000, 0), ("L-002", 45000, 0), ("D-001", 0, 180000)} <= money
    rent = next(t for t in cmp["tieouts"] if t["id"] == "AGG-2")
    assert rent["legacy"] == rent["simulated"] == 38711500 and rent["actual"] == 38691000
    g = _gates(client, did)
    assert g["G9"] == "pass" and g["G10"] == "fail" and g["G11"] == "fail"
    assert client.post(_base(did) + "/receipt", headers=WS, json={"reviewer": "Ana"}).status_code == 409

    _close_findings(client, did)
    g = _gates(client, did)
    assert g["G10"] == "pass" and g["G11"] == "pass"
    r = client.post(_base(did) + "/receipt", headers=WS, json={"reviewer": "Ana"})
    assert r.status_code == 201, r.text
    rc = r.json()
    body = rc["receipt"]
    assert body["plan"]["hash"] == dr["plan_hash"] and body["dry_run"]["id"] == dr["id"]
    assert body["export"]["files"]["import/cedar_import_leases.csv"] == next(
        f["sha256"] for f in man["files"] if f["path"] == "import/cedar_import_leases.csv")
    assert any("not an audit opinion" in x for x in body["limits"])
    got = client.get(_base(did) + "/receipt", headers=WS).json()
    assert got["verifies"] and got["sha256"] == rc["sha256"]
    with db.connect() as conn:  # tampering with the stored receipt is detectable
        conn.execute("UPDATE release_receipts SET receipt_json=replace(receipt_json, 'Ana', 'Eve')")
    assert client.get(_base(did) + "/receipt", headers=WS).json()["verifies"] is False


def test_edits_after_signoff_invalidate_it(client):
    did = _sample(client)
    _plan(client, did)
    _decide_all(client, did)
    _dry(client, did)
    client.post(_base(did) + "/signoff", headers=WS, json={"reviewer": "Ana", "acknowledge": True})
    assert _gates(client, did)["G8"] == "pass"
    _act(client, did, "work_orders.description", "reopen")
    _act(client, did, "work_orders.description", "ignore", note="Descriptions re-entered by the Cedar team.")
    g = _gates(client, did)
    assert g["G4"] == "fail" and g["G8"] == "fail"
    assert client.get(_base(did) + "/export.zip", headers=WS).status_code == 409


def test_blank_deposit_is_not_zero_and_formula_cells_are_guarded(client):
    did = _sample(client)
    with db.connect() as conn:
        sid = conn.execute("SELECT id FROM snapshots WHERE deal_id=? AND side='before' AND entity='deposits'",
                           (did,)).fetchone()[0]
        rows = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT row_number, raw_json FROM source_rows WHERE snapshot_id=?", (sid,))}
        rows[2]["deposit_balance"] = ""       # unknown
        rows[3]["deposit_balance"] = "0.00"   # genuinely none held
        for n in (2, 3):
            conn.execute("UPDATE source_rows SET raw_json=? WHERE snapshot_id=? AND row_number=?",
                         (db.dumps(rows[n]), sid, n))
        wo = conn.execute("SELECT id FROM snapshots WHERE deal_id=? AND side='before' AND entity='maintenance'",
                          (did,)).fetchone()[0]
        raw = json.loads(conn.execute("SELECT raw_json FROM source_rows WHERE snapshot_id=? AND row_number=2",
                                      (wo,)).fetchone()[0])
        raw["summary"] = '=HYPERLINK("http://example.invalid","click")'
        conn.execute("UPDATE source_rows SET raw_json=? WHERE snapshot_id=? AND row_number=2", (db.dumps(raw), wo))
    _plan(client, did)
    _decide_all(client, did)
    dr = _dry(client, did)
    pf = {(g["check"], g["table"]): g for g in dr["preflight"]}
    assert pf[("PF-DEP", "security_deposits")]["rows"] == 1 and pf[("PF-DEP", "security_deposits")]["blocking"]
    prev = client.get(_base(did) + "/dry-run/preview?table=security_deposits&limit=5", headers=WS).json()
    assert prev["rows"][0]["deposit_held"] == "0.00"   # the zero row survives as zero
    wo_prev = client.get(_base(did) + "/dry-run/preview?table=work_orders&limit=1", headers=WS).json()
    assert wo_prev["rows"][0]["description"].startswith("=")  # simulation keeps the value; export guards it
    assert wo_prev["lineage"][0]["columns"]["description"] == {"sources": ["summary"], "op": "copy"}


def test_formula_guard_in_export(client):
    did = _sample(client)
    with db.connect() as conn:
        wo = conn.execute("SELECT id FROM snapshots WHERE deal_id=? AND side='before' AND entity='maintenance'",
                          (did,)).fetchone()[0]
        raw = json.loads(conn.execute("SELECT raw_json FROM source_rows WHERE snapshot_id=? AND row_number=2",
                                      (wo,)).fetchone()[0])
        raw["summary"] = "=1+1"
        conn.execute("UPDATE source_rows SET raw_json=? WHERE snapshot_id=? AND row_number=2", (db.dumps(raw), wo))
    _plan(client, did)
    _decide_all(client, did)
    _dry(client, did)
    client.post(_base(did) + "/signoff", headers=WS, json={"reviewer": "Ana", "acknowledge": True})
    zf = zipfile.ZipFile(io.BytesIO(client.get(_base(did) + "/export.zip", headers=WS).content))
    wo_csv = zf.read("mergeproof_import/import/cedar_import_work_orders.csv").decode()
    assert ",'=1+1," in wo_csv
    man = json.loads(zf.read("mergeproof_import/manifest.json"))
    assert man["formula_guarded_cells"]["cedar_import_work_orders.csv"] == 1


def test_source_drift_blocks_until_rebased(client):
    did = _sample(client)
    _plan(client, did)
    _decide_all(client, did)
    with db.connect() as conn:
        snap = dict(conn.execute("SELECT * FROM snapshots WHERE deal_id=? AND side='before' AND entity='units'",
                                 (did,)).fetchone())
        headers = [h for h in json.loads(snap["headers_json"]) if h != "bedrooms"]
        conn.execute("UPDATE snapshots SET headers_json=?, content_hash='changed' WHERE id=?",
                     (db.dumps(headers), snap["id"]))
    plan = client.get(_base(did) + "/plan", headers=WS).json()
    assert plan["drift"]["changed"] and any("bedrooms" in i["issue"] for i in plan["drift"]["issues"])
    assert _gates(client, did)["G1"] == "fail"
    assert client.post(_base(did) + "/dry-run", headers=WS, json={"reviewer": "Ana"}).status_code == 409
    r = client.post(_base(did) + "/plan/rebase", headers=WS, json={"reviewer": "Ana"})
    assert r.status_code == 200 and not r.json()["drift"]["changed"]
    beds = _item(r.json(), "units.bedrooms")
    assert beds["status"] != "approved" and beds["sources"] == []


def test_derived_template_from_consolidated_exports(client):
    did = _sample(client)
    t = client.get(_base(did) + "/templates", headers=WS).json()
    assert any(x["id"] == "derived-from-consolidated" and x["available"] for x in t)
    r = client.post(_base(did) + "/plan", headers=WS, json={"template_id": "derived-from-consolidated",
                                                            "reviewer": "Ana"})
    assert r.status_code == 201
    plan = r.json()
    assert {tb["name"] for tb in plan["template_tables"]} >= {"leases", "tenants", "units"}
    lease_rent = next(i for i in plan["items"] if i["table"] == "leases" and i["canonical"] == "monthly_rent")
    assert lease_rent["column"] == "Base Rent" and lease_rent["sources"] == ["monthly_rent"]
    dr = _dry(client, did)
    assert dr["totals"]["source_rows"] > 0


def test_integrate_routes_are_workspace_scoped(client):
    did = _sample(client)
    _plan(client, did)
    for path in ("", "/plan", "/profile", "/readiness", "/verify", "/events", "/export.zip", "/blueprint.json"):
        assert client.get(_base(did) + path, headers=OTHER).status_code == 404, path
    assert client.post(_base(did) + "/dry-run", headers=OTHER, json={"reviewer": "x"}).status_code == 404
    assert client.post(_base(did) + "/plan/items/units.unit_code", headers=OTHER,
                       json={"action": "approve", "reviewer": "x", "note": "x"}).status_code == 404
    assert client.get(_base(did) + "/plan").status_code == 400  # no workspace at all


def test_reports_carry_the_integration_section(client):
    did = _sample(client)
    _plan(client, did)
    _decide_all(client, did)
    _dry(client, did)
    client.post(_base(did) + "/signoff", headers=WS, json={"reviewer": "Ana", "acknowledge": True})
    client.post(f"/api/deals/{did}/audit", headers=WS)
    rep = client.get(f"/api/deals/{did}/report", headers=WS).json()
    ig = rep["integration"]
    assert ig["dry_run"]["banner"].startswith("SIMULATED OUTPUT") and ig["comparison"]["counts"]["observed_only"] > 0
    assert all(r["predicted_in_dry_run"] is False for r in rep["findings"])
    csv_text = client.get(f"/api/deals/{did}/findings.csv", headers=WS).content.decode("utf-8-sig")
    header = csv_text.splitlines()[0].split(",")
    assert header[-3:] == ["material", "material_reason", "predicted_in_dry_run"]
    assert ",yes,rent ≥ $500/mo,no" in csv_text
    pdf = client.get(f"/api/deals/{did}/report.pdf", headers=WS)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and len(pdf.content) > 20000


def test_medium_predicted_findings_block_signoff(client):
    """A wrong date column rejects nothing and ties out, but predicts ~175 L-005 findings — G7 must stop it."""
    did = _sample(client)
    _plan(client, did)
    r = _act(client, did, "leases.move_in_date", "edit", sources=["end_date"], note="Looks like the start column.")
    assert r.status_code == 200
    _decide_all(client, did)
    dr = _dry(client, did)
    assert dr["totals"]["rejected"] == 0 and dr["predicted_kpis"]["medium"] >= 100
    assert dr["predicted_kpis"]["critical"] + dr["predicted_kpis"]["high"] == 0
    g = _gates(client, did)
    assert g["G5"] == "pass" and g["G7"] == "fail"
    assert client.post(_base(did) + "/signoff", headers=WS,
                       json={"reviewer": "Ana", "acknowledge": True}).status_code == 409


def test_reference_backed_key_is_offered_when_the_audit_mapping_is_wrong(client):
    did = _sample(client)
    deal = client.get(f"/api/deals/{did}", headers=WS).json()
    units = next(s for s in deal["snapshots"] if s["side"] == "before" and s["entity"] == "units")
    mapping = {**units["mapping"], "unit_id": "unit_code"}   # the audit itself bound the door code
    assert client.patch(f"/api/snapshots/{units['id']}", headers=WS,
                        json={"entity": "units", "mapping": mapping}).status_code == 200
    plan = _plan(client, did)
    key = _item(plan, "units.unit_code")
    assert key["status"] == "needs_review" and key["sources"] == ["unit_code"]
    alt = next(c for c in key["candidates"] if c["sources"] == ["unit_id"])
    assert any(s["kind"] == "references" and s["ratio"] == 1.0 for s in alt["signals"])
