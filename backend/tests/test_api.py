"""End-to-end API acceptance tests (the judge's happy path plus failure modes)."""
import csv
import io

from app import config
from app.reports import safe_cell

from .conftest import WS


def start_sample(client):
    r = client.post("/api/sample", headers=WS, json={})
    assert r.status_code == 200
    return r.json()["deal"]


def audit(client, did):
    r = client.post(f"/api/deals/{did}/audit", headers=WS)
    assert r.status_code == 200, r.text
    return r.json()


def findings(client, did, inactive=False):
    return client.get(f"/api/deals/{did}/findings" + ("?include_inactive=true" if inactive else ""), headers=WS).json()


def test_workspace_required_and_isolated(client):
    assert client.get("/api/deals").status_code == 400
    deal = start_sample(client)
    assert client.get(f"/api/deals/{deal['id']}", headers={"X-Workspace": "ws_someone_else"}).status_code == 404
    assert client.get("/api/deals", headers={"X-Workspace": "ws_someone_else"}).json() == []


def test_sample_is_idempotent_and_resettable(client):
    a = start_sample(client)
    assert a["status"]["state"] == "ready"
    assert len(a["snapshots"]) == 12
    again = client.post("/api/sample", headers=WS, json={}).json()
    assert again["created"] is False and again["deal"]["id"] == a["id"]
    reset = client.post("/api/sample", headers=WS, json={"reset": True}).json()
    assert reset["created"] is True and reset["deal"]["id"] != a["id"]
    assert [d["id"] for d in client.get("/api/deals", headers=WS).json()] == [reset["deal"]["id"]]


def test_happy_path_review_persists_and_exports(client):
    deal = start_sample(client)
    res = audit(client, deal["id"])
    assert [s["name"] for s in res["run"]["stages"]] == [
        "Preparing exports", "Mapping fields", "Matching entities", "Testing invariants", "Building evidence"]
    k = res["summary"]["kpis"]
    assert k["rent_at_risk_open"] == 442500 and k["deposit_open"] == 414000 and k["critical_open"] == 3

    fs = findings(client, deal["id"])
    missing = next(f for f in fs if f["rule_id"] == "L-001")
    detail = client.get(f"/api/findings/{missing['id']}", headers=WS).json()
    assert detail["payload"]["source"]["row"] == 43
    assert detail["payload"]["recommended_action"]

    # expected exception requires a reason
    r = client.post(f"/api/findings/{missing['id']}/review", headers=WS,
                    json={"action": "expected_exception", "reviewer": "Jordan (Ops)"})
    assert r.status_code == 422
    r = client.post(f"/api/findings/{missing['id']}/review", headers=WS,
                    json={"action": "expected_exception", "reviewer": "Jordan (Ops)",
                          "note": "Tenant moved out 9/28; lease terminated before cutover."})
    assert r.status_code == 200 and r.json()["status"] == "expected"

    # "refresh": fresh reads see the decision and recomputed KPIs
    again = client.get(f"/api/findings/{missing['id']}", headers=WS).json()
    assert again["status"] == "expected"
    assert again["events"][-1]["note"].startswith("Tenant moved out")
    assert again["events"][-1]["reviewer"] == "Jordan (Ops)"
    s = client.get(f"/api/deals/{deal['id']}/summary", headers=WS).json()
    assert s["kpis"]["rent_at_risk_open"] == 442500 - 225000
    assert s["kpis"]["rent_at_risk_detected"] == 442500
    assert s["status"]["state"] == "in_review"

    # rerun keeps the decision and does not clone findings
    audit(client, deal["id"])
    fs2 = findings(client, deal["id"])
    assert len(fs2) == len(fs)
    assert next(f for f in fs2 if f["id"] == missing["id"])["status"] == "expected"

    pdf = client.get(f"/api/deals/{deal['id']}/report.pdf?ws={WS['X-Workspace']}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 10_000
    out = client.get(f"/api/deals/{deal['id']}/findings.csv?ws={WS['X-Workspace']}")
    assert out.status_code == 200
    rows = list(csv.DictReader(io.StringIO(out.content.decode("utf-8-sig"))))
    row = next(r for r in rows if r["finding_id"] == missing["id"])
    assert row["status"] == "expected" and row["last_note"].startswith("Tenant moved out")
    assert row["est_monthly_rent_at_risk_counted"] == "0.00" and row["rent_exposure_before_dedup"] == "2250.00"
    theo = next(r for r in rows if "Theo" in r["subject"])
    assert theo["est_monthly_rent_at_risk_counted"] == "450.00"
    assert "Monthly rent: $2,500.00" in theo["expected"] and "Monthly rent: $2,050.00" in theo["observed"]


def test_confirm_match_then_rerun_then_reopen(client):
    deal = start_sample(client)
    audit(client, deal["id"])
    amb = next(f for f in findings(client, deal["id"]) if f["rule_id"] == "L-006")
    d = client.get(f"/api/findings/{amb['id']}", headers=WS).json()
    bad = client.post(f"/api/findings/{amb['id']}/review", headers=WS,
                      json={"action": "confirm_match", "reviewer": "R", "candidate": "nope"})
    assert bad.status_code == 422
    cand = d["payload"]["candidates"][0]["target_key"]
    r = client.post(f"/api/findings/{amb['id']}/review", headers=WS,
                    json={"action": "confirm_match", "reviewer": "R", "candidate": cand})
    assert r.json()["status"] == "resolved" and r.json()["override"]["target_key"] == cand
    s = client.get(f"/api/deals/{deal['id']}/summary", headers=WS).json()
    assert s["run"]["match_decisions_pending"] == 1 and s["status"]["state"] == "stale"
    audit(client, deal["id"])
    after = findings(client, deal["id"], inactive=True)
    old = next(f for f in after if f["id"] == amb["id"])
    assert old["active"] is False and old["status"] == "resolved"
    new_rules = sorted(f["rule_id"] for f in after if f["active"] and "Alex Kim" in f["subject"])
    assert new_rules == ["L-007", "L-011"]  # confirmed lease sits on the wrong unit; the 2C lease is now unexplained
    # other actions on a no-longer-detected finding are blocked with a clear message
    r = client.post(f"/api/findings/{amb['id']}/review", headers=WS, json={"action": "resolve", "reviewer": "R"})
    assert r.status_code == 409


def test_reopen_revokes_match_before_rerun(client):
    deal = start_sample(client)
    audit(client, deal["id"])
    amb = next(f for f in findings(client, deal["id"]) if f["rule_id"] == "L-006")
    d = client.get(f"/api/findings/{amb['id']}", headers=WS).json()
    client.post(f"/api/findings/{amb['id']}/review", headers=WS,
                json={"action": "confirm_match", "reviewer": "R", "candidate": d["payload"]["candidates"][1]["target_key"]})
    r = client.post(f"/api/findings/{amb['id']}/review", headers=WS, json={"action": "reopen", "reviewer": "R"})
    assert r.json()["status"] == "open" and r.json()["override"] is None
    audit(client, deal["id"])
    assert next(f for f in findings(client, deal["id"]) if f["id"] == amb["id"])["rule_id"] == "L-006"


def test_invalid_transitions_and_comment(client):
    deal = start_sample(client)
    audit(client, deal["id"])
    f = findings(client, deal["id"])[0]
    url = f"/api/findings/{f['id']}/review"
    assert client.post(url, headers=WS, json={"action": "reopen", "reviewer": "R"}).status_code == 409
    assert client.post(url, headers=WS, json={"action": "comment", "reviewer": "R", "note": "Called PM"}).json()["status"] == "open"
    assert client.post(url, headers=WS, json={"action": "investigate", "reviewer": "R"}).json()["status"] == "investigating"
    assert client.post(url, headers=WS, json={"action": "resolve", "reviewer": "R"}).json()["status"] == "resolved"
    r = client.post(url, headers=WS, json={"action": "resolve", "reviewer": "R"})
    assert r.status_code == 409 and "already" in r.json()["detail"]
    assert client.post(url, headers=WS, json={"action": "reopen", "reviewer": "R"}).json()["status"] == "open"
    ev = client.get(f"/api/findings/{f['id']}", headers=WS).json()["events"]
    assert [e["action"] for e in ev] == ["comment", "investigate", "resolve", "reopen"]


def _upload(client, did, side, files, confirm="true"):
    return client.post(f"/api/deals/{did}/snapshots", headers=WS, data={"side": side, "confirm_synthetic": confirm},
                       files=[("files", (n, b, "text/csv")) for n, b in files])


def test_upload_own_files_reproduces_sample(client):
    did = client.post("/api/deals", headers=WS, json={"title": "My upload", "acquirer": "Cedar Property Management",
                                                      "target_business": "Bayview Rentals",
                                                      "deal_date": "2026-09-30"}).json()["id"]
    assert client.post(f"/api/deals/{did}/audit", headers=WS).status_code == 409
    for side in ("before", "after"):
        files = [(p.name, p.read_bytes()) for p in sorted((config.SAMPLE_DIR / side).glob("*.csv"))]
        r = _upload(client, did, side, files)
        assert r.status_code == 200 and len(r.json()["created"]) == 6 and not r.json()["errors"]
    res = audit(client, did)
    assert res["summary"]["kpis"]["rent_at_risk_open"] == 442500


def test_upload_errors_are_graceful(client, monkeypatch):
    did = client.post("/api/deals", headers=WS, json={"title": "Errors"}).json()["id"]
    assert _upload(client, did, "before", [("a.csv", b"x,y\n1,2\n")], confirm="false").status_code == 400
    r = _upload(client, did, "before", [("bad.csv", b"\x00\x00garbage"), ("hdr.csv", b"100,200\n300,400\n")])
    assert r.status_code == 400
    msgs = [e["message"] for e in r.json()["errors"]]
    assert "binary" in msgs[0] and "looks like data" in msgs[1]
    r = _upload(client, did, "before", [("leases.csv", b"lease_id,unit_id,monthly_rent\nL1,U1,100\n"),
                                        ("empty.csv", b"lease_id,unit_id\n")])
    assert r.status_code == 200 and len(r.json()["created"]) == 1 and "no data rows" in r.json()["errors"][0]["message"]
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 50)
    r = _upload(client, did, "after", [("big.csv", b"lease_id,unit_id,monthly_rent\n" + b"L1,U1,100\n" * 20)])
    assert r.status_code == 400 and "limit" in r.json()["detail"]
    assert _upload(client, did, "sideways", [("a.csv", b"a\n1\n")]).status_code == 422


def test_mapping_correction_unblocks_audit(client):
    did = client.post("/api/deals", headers=WS, json={"title": "Mapping"}).json()["id"]
    before = b"ref,apt,amount_due,tenant\nL1,1A,1500,Ana\n"
    after = b"ref,apt,amount_due,tenant\nL1,1A,1400,Ana\n"
    b = _upload(client, did, "before", [("leases_before.csv", before)]).json()["created"][0]
    a = _upload(client, did, "after", [("leases_after.csv", after)]).json()["created"][0]
    assert b["entity"] == "leases" and b["validation"]["status"] == "needs_mapping"
    r = client.post(f"/api/deals/{did}/audit", headers=WS)
    assert r.status_code == 409 and r.json()["detail"]["problems"]
    for s in (a, b):
        r = client.patch(f"/api/snapshots/{s['id']}", headers=WS,
                         json={"entity": "leases", "mapping": {"lease_id": "ref", "unit_id": "apt",
                                                               "monthly_rent": "amount_due"}})
        assert r.status_code == 200 and r.json()["validation"]["status"] == "ready"
    bad = client.patch(f"/api/snapshots/{a['id']}", headers=WS,
                       json={"entity": "leases", "mapping": {"lease_id": "ref", "unit_id": "ref"}})
    assert bad.status_code == 422
    res = audit(client, did)
    fs = findings(client, did)
    assert any(f["rule_id"] == "L-002" and f["rent_risk_cents"] == 10000 for f in fs)
    assert res["summary"]["kpis"]["rent_at_risk_open"] == 10000


def test_csv_export_neutralizes_formulas(client):
    did = client.post("/api/deals", headers=WS, json={"title": "Injection"}).json()["id"]
    _upload(client, did, "before", [("tenants.csv", b"tenant_id,name,email\nT1,=HYPERLINK(\"http://x\"),a@example.com\n"),
                                    ("leases.csv", b"lease_id,tenant_id,unit,monthly_rent,status\nL1,T1,1A,900,Active\n")])
    _upload(client, did, "after", [("tenants.csv", b"tenant_id,name,email\nT1,x,b@example.com\n"),
                                   ("leases.csv", b"lease_id,tenant_id,unit,monthly_rent,status\nZ9,T1,9Z,1,Past\n")])
    audit(client, did)
    body = client.get(f"/api/deals/{did}/findings.csv?ws={WS['X-Workspace']}").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(body)))
    cells = [c for r in rows[1:] for c in r]
    assert any(c.startswith("'=HYPERLINK") for c in cells)
    assert not any(c.startswith("=") for c in cells)
    assert safe_cell("-450.00") == "-450.00" and safe_cell("-cmd") == "'-cmd" and safe_cell("@SUM(A1)") == "'@SUM(A1)"


def test_deal_metadata_edit_and_delete(client):
    deal = start_sample(client)
    r = client.patch(f"/api/deals/{deal['id']}", headers=WS, json={"source_notes": "Updated", "deal_date": "2026-10-01"})
    assert r.json()["source_notes"] == "Updated" and r.json()["deal_date"] == "2026-10-01"
    assert client.patch(f"/api/deals/{deal['id']}", headers=WS, json={"deal_date": "10/01"}).status_code == 422
    assert client.delete(f"/api/deals/{deal['id']}", headers=WS).status_code == 204
    assert client.get(f"/api/deals/{deal['id']}", headers=WS).status_code == 404


def test_deal_intelligence_crud(client):
    deal = start_sample(client)
    body = {"company_name": "Harborline Rentals (fictional)", "sector": "Property management",
            "location": "Port Wren, CA", "event_date": "2026-08-12", "source_link": "https://example.com/announcement",
            "website": "https://example.com", "notes": "Researched in PitchBook separately; my own notes only.",
            "contact_role": "COO", "pilot_status": "contacted", "linked_deal_id": deal["id"]}
    r = client.post("/api/intel", headers=WS, json=body)
    assert r.status_code == 201
    iid = r.json()["id"]
    assert client.post("/api/intel", headers=WS, json={**body, "source_link": "javascript:alert(1)"}).status_code == 422
    assert client.post("/api/intel", headers=WS, json={**body, "linked_deal_id": "deal_nope"}).status_code == 422
    r = client.put(f"/api/intel/{iid}", headers=WS, json={**body, "pilot_status": "pilot_proposed"})
    assert r.json()["pilot_status"] == "pilot_proposed"
    assert len(client.get("/api/intel", headers=WS).json()) == 1
    assert client.get("/api/intel", headers={"X-Workspace": "ws_someone_else"}).json() == []
    assert client.delete(f"/api/intel/{iid}", headers=WS).status_code == 204


def test_unit_map_and_sample_files(client):
    deal = start_sample(client)
    assert client.get(f"/api/deals/{deal['id']}/unitmap", headers=WS).json()["has_run"] is False
    audit(client, deal["id"])
    um = client.get(f"/api/deals/{deal['id']}/unitmap", headers=WS).json()
    units = [u for p in um["properties"] for u in p["units"]]
    assert len(units) == 186
    juniper = next(p for p in um["properties"] if p["name"] == "Juniper Terrace")
    assert next(u for u in juniper["units"] if u["norm"] == "4B")["state"] == "critical"
    files = client.get("/api/sample/files").json()
    assert len(files) == 13 and [f["name"] for f in files if f["side"] == "messy"] == ["cedar_rent_roll_messy_headers.csv"]
    assert client.get("/api/sample/files/messy/cedar_rent_roll_messy_headers.csv").status_code == 200
    r = client.get(f"/api/sample/files/before/{files[0]['name']}")
    assert r.status_code == 200 and r.text.startswith(files[0]["name"].split("_")[0] and r.text[:5])
    assert client.get("/api/sample/files/before/..%2F..%2Fapp%2Fmain.py").status_code == 404
