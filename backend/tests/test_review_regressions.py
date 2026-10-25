"""Regression tests for defects found in the independent engine/API review."""
import io
import time

import pytest
from openpyxl import Workbook

from app import config, db, ingest
from app import normalize as nz

from .conftest import WS


def deal(client, **kw):
    return client.post("/api/deals", headers=WS, json={"title": "Regression", "deal_date": "2026-09-30", **kw}).json()["id"]


def upload(client, did, side, files):
    r = client.post(f"/api/deals/{did}/snapshots", headers=WS, data={"side": side, "confirm_synthetic": "true"},
                    files=[("files", (n, b.encode() if isinstance(b, str) else b, "text/csv")) for n, b in files])
    assert r.status_code == 200, r.text
    return r.json()


def audit(client, did):
    r = client.post(f"/api/deals/{did}/audit", headers=WS)
    assert r.status_code == 200, r.text
    return r.json()["summary"]


def findings(client, did, inactive=False):
    return client.get(f"/api/deals/{did}/findings" + ("?include_inactive=true" if inactive else ""), headers=WS).json()


def rules(fs):
    return sorted(f["rule_id"] for f in fs)


LEASES_OK = "lease_id,unit_id,monthly_rent,status\nL1,U1,1000,Active\n"


def test_duplicate_id_plus_bad_value_does_not_crash(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,unit_id,monthly_rent,status\n"
                                                  "L1,1A,1000,Active\nL1,1B,1100,Active\nL2,1C,abc,Active\n")])
    upload(client, did, "after", [("leases.csv", "lease_id,unit_id,monthly_rent,status\n"
                                                 "L1,1A,1000,Active\nL2,1C,1200,Active\n")])
    audit(client, did)
    q1 = next(f for f in findings(client, did) if f["rule_id"] == "Q-001")
    members = client.get(f"/api/findings/{q1['id']}", headers=WS).json()["payload"]["members"]
    assert [m["row"] for m in members] == [2, 3]
    assert "Q-002" in rules(findings(client, did))


def test_xlsx_stray_far_cell_is_cheap_and_wide_sheets_rejected():
    wb = Workbook()
    ws = wb.active
    ws.append(["lease_id", "unit_id", "monthly_rent"])
    ws.append(["L1", "U1", 100])
    ws["XFD19000"] = "x"
    buf = io.BytesIO()
    wb.save(buf)
    t0 = time.time()
    [t] = ingest.parse_upload("far.xlsx", buf.getvalue())
    assert time.time() - t0 < 5 and len(t.headers) == 3
    wb = Workbook()
    ws = wb.active
    ws.append(["c"] * (config.MAX_COLUMNS + 1))
    ws.append(["v"] * (config.MAX_COLUMNS + 1))
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ingest.UploadError, match="columns"):
        ingest.parse_upload("wide.xlsx", buf.getvalue())


def test_confirmed_match_can_be_undone_after_rerun(client):
    did = client.post("/api/sample", headers=WS, json={}).json()["deal"]["id"]
    audit(client, did)
    amb = next(f for f in findings(client, did) if f["rule_id"] == "L-006")
    cand = client.get(f"/api/findings/{amb['id']}", headers=WS).json()["payload"]["candidates"][0]["target_key"]
    client.post(f"/api/findings/{amb['id']}/review", headers=WS,
                json={"action": "confirm_match", "reviewer": "R", "candidate": cand})
    audit(client, did)
    assert not next(f for f in findings(client, did, True) if f["id"] == amb["id"])["active"]
    r = client.post(f"/api/findings/{amb['id']}/review", headers=WS, json={"action": "reopen", "reviewer": "R"})
    assert r.status_code == 200 and r.json()["status"] == "open" and r.json()["override"] is None
    audit(client, did)
    back = next(f for f in findings(client, did) if f["id"] == amb["id"])
    assert back["active"] and back["rule_id"] == "L-006"
    # a second reopen on an inactive finding without a live override is still refused
    assert client.post(f"/api/findings/{amb['id']}/review", headers=WS,
                       json={"action": "comment", "reviewer": "R", "note": "Checked with the PM."}).status_code == 200


def test_leases_only_with_rekeyed_property_ids(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,property,unit,monthly_rent,status\n"
                                                  "A1,BV-P-01,1A,1000,Active\nA2,BV-P-01,1B,1100,Active\n"
                                                  "A3,BV-P-02,1A,1200,Active\n")])
    upload(client, did, "after", [("leases.csv", "lease_id,property,unit,monthly_rent,status\n"
                                                 "Z1,CPM-B080,1A,1000,Current\nZ2,CPM-B080,1B,1100,Current\n"
                                                 "Z3,CPM-B070,1A,1200,Current\n")])
    s = audit(client, did)
    fs = findings(client, did)
    # 1B is unique, so it pairs; 1A exists in both buildings and the property IDs were re-keyed, so a human decides.
    assert s["counts"]["leases"]["confirmed"] == 1
    assert sorted(f["subject"] for f in fs if f["rule_id"] == "L-006") == ["Unit 1A · BV-P-01", "Unit 1A · BV-P-02"]
    assert not [f for f in fs if f["rule_id"] == "L-007"]


def test_leases_only_with_shared_property_refs_disambiguates(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,property,unit,monthly_rent,status\n"
                                                  "A1,Elm Court,1A,1000,Active\nA3,Oak House,1A,1200,Active\n")])
    upload(client, did, "after", [("leases.csv", "lease_id,property,unit,monthly_rent,status\n"
                                                 "Z1,Elm Court,1A,950,Current\nZ3,Oak House,1A,1200,Current\n")])
    s = audit(client, did)
    assert s["counts"]["leases"]["confirmed"] == 2
    assert [(f["rule_id"], f["rent_risk_cents"]) for f in findings(client, did)] == [("L-002", 5000)]


def test_units_without_properties_export(client):
    did = deal(client)
    before_units = "unit_id,property_id,unit_number\nU1,P1,1A\nU2,P1,1B\n"
    after_units = "unit_id,property_id,unit_number\nX1,B9,1A\nX2,B9,1B\n"
    upload(client, did, "before", [("units.csv", before_units), ("leases.csv",
                                    "lease_id,unit_id,monthly_rent,status,start_date\nL1,U1,1000,Active,2026-01-01\n"
                                    "L2,U2,900,Active,2026-02-01\n")])
    upload(client, did, "after", [("units.csv", after_units), ("leases.csv",
                                   "lease_id,unit_id,monthly_rent,status,start_date\nQ1,X1,1000,Current,2026-01-01\n"
                                   "Q2,X2,800,Current,2026-02-01\n")])
    s = audit(client, did)
    assert s["counts"]["units"]["confirmed"] == 2
    assert [(f["rule_id"], f["rent_risk_cents"]) for f in findings(client, did)] == [("L-002", 10000)]


def test_tenant_keyed_deposits_without_tenants_export(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,tenant_id,unit,monthly_rent,status\nL1,T1,1A,1000,Active\n"),
                                   ("deposits.csv", "tenant_id,deposit_balance\nT1,2400\nT9,500\n")])
    upload(client, did, "after", [("leases.csv", "lease_id,tenant_id,unit,monthly_rent,status\nQ1,T1,1A,1000,Current\n"),
                                  ("deposits.csv", "tenant_id,deposit_balance\nT1,100\n")])
    s = audit(client, did)
    fs = findings(client, did)
    d2 = next(f for f in fs if f["rule_id"] == "D-002")
    assert d2["deposit_cents"] == 230000 and s["kpis"]["deposit_open"] == 230000
    assert "D-004" in rules(fs)  # T9's $500 can't be linked to any lease — surfaced, not dropped


def test_duplicate_legacy_rows_count_rent_once(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,unit_id,monthly_rent,status\nL2,9Z,1000,Active\n"
                                                  "L2,9Z,1000,Active\nL1,1A,500,Active\n")])
    upload(client, did, "after", [("leases.csv", "lease_id,unit_id,monthly_rent,status\nL1,1A,500,Active\n")])
    s = audit(client, did)
    assert s["kpis"]["rent_at_risk_open"] == 100000


def test_optional_column_missing_on_one_side_is_not_a_change(client):
    did = deal(client)
    upload(client, did, "before", [
        ("leases.csv", "lease_id,unit,monthly_rent,status,start_date,end_date\nL1,1A,1000,Active,2026-01-01,2026-12-31\n"),
        ("work_orders.csv", "ticket_id,unit,summary,priority,status,opened_on\nW1,1A,No heat,Urgent,Open,2026-09-01\n")])
    upload(client, did, "after", [
        ("leases.csv", "lease_id,unit,monthly_rent,status,start_date\nL1,1A,1000,Active,2026-01-01\n"),
        ("work_orders.csv", "ticket_id,unit,summary,status,opened_on\nW1,1A,No heat,Open,2026-09-01\n")])
    audit(client, did)
    assert not [f for f in findings(client, did) if f["rule_id"] in ("L-005", "M-003")]


def test_near_tie_at_confirm_level_goes_to_review(client):
    did = deal(client)
    tenants_b = ("tenant_id,full_name,email,phone\nT-ANA,Ana Ruiz,home@example.com,415-555-0100\n"
                 "T-BEN,Ben Ruiz,home@example.com,415-555-0100\n")
    upload(client, did, "before", [("tenants.csv", tenants_b), ("leases.csv", LEASES_OK)])
    upload(client, did, "after", [("tenants.csv", "tenant_id,full_name,email,phone\nR1,A. Ruiz,home@example.com,4155550100\n"),
                                  ("leases.csv", LEASES_OK)])
    s = audit(client, did)
    assert s["counts"]["tenants"]["confirmed"] == 0 and s["counts"]["tenants"]["needs_review"] == 2


@pytest.mark.parametrize("raw,state", [("Current Resident", "active"), ("Notice-Rented", "active"),
                                       ("Eviction", "active"), ("Past Resident", "expired")])
def test_status_vocabulary(raw, state):
    assert nz.lease_status(raw) == state


def test_unrecognized_statuses_go_to_review(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,unit,monthly_rent,status\nL1,1A,1000,Active\n"
                                                  "L2,2B,900,Zorbled\n")])
    upload(client, did, "after", [("leases.csv", "lease_id,unit,monthly_rent,status\nL1,1A,1000,Floob\n")])
    s = audit(client, did)
    fs = findings(client, did)
    l004 = next(f for f in fs if f["rule_id"] == "L-004")
    assert l004["title"] == "Lease status not recognized" and l004["rent_risk_cents"] == 0
    l016 = next(f for f in fs if f["rule_id"] == "L-016")
    assert l016["pending_rent_cents"] == 90000 and l016["confidence"] == "needs_review"
    assert s["kpis"]["rent_at_risk_open"] == 0 and s["kpis"]["pending_review_rent"] == 190000


def test_pdf_with_very_long_cell_values(client):
    did = deal(client)
    long = "Leak " * 700
    upload(client, did, "before", [("leases.csv", LEASES_OK),
                                   ("work_orders.csv", f"ticket_id,unit_id,summary,status\nW1,U1,{long},Open\n")])
    upload(client, did, "after", [("leases.csv", LEASES_OK),
                                  ("work_orders.csv", "ticket_id,unit_id,summary,status\nW9,U7,Other,Open\n")])
    audit(client, did)
    r = client.get(f"/api/deals/{did}/report.pdf?ws={WS['X-Workspace']}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_money_parsing_edge_cases():
    assert nz.parse_money("2400,00") == 240000
    assert nz.parse_money("1.250,00") == 125000
    assert nz.parse_money("1,250.00") == 125000
    for bad in ("2,400,00", "99999999999999999999", "12,34,567"):
        with pytest.raises(nz.MoneyError):
            nz.parse_money(bad)
    assert nz.parse_date("45292") == "2024-01-01"
    assert nz.unit_label("North 3") == "NORTH3" and nz.unit_label("APT4B") == "4B"


def test_huge_rent_is_a_data_quality_finding_not_a_crash(client):
    did = deal(client)
    upload(client, did, "before", [("leases.csv", "lease_id,unit_id,monthly_rent,status\nL1,U1,99999999999999999999,Active\n")])
    upload(client, did, "after", [("leases.csv", LEASES_OK)])
    audit(client, did)
    assert "Q-002" in rules(findings(client, did))


def test_rereview_counts_as_open(client):
    did = client.post("/api/sample", headers=WS, json={}).json()["deal"]["id"]
    audit(client, did)
    fs = findings(client, did)
    for f in fs:
        client.post(f"/api/findings/{f['id']}/review", headers=WS, json={"action": "resolve", "reviewer": "R"})
    s = client.get(f"/api/deals/{did}/summary", headers=WS).json()
    assert s["status"]["state"] == "complete" and s["kpis"]["rent_at_risk_open"] == 0
    target = next(f for f in fs if f["rule_id"] == "L-001")
    with db.connect() as conn:
        conn.execute("UPDATE findings SET needs_rereview=1 WHERE id=?", (target["id"],))
    s = client.get(f"/api/deals/{did}/summary", headers=WS).json()
    assert s["status"]["state"] != "complete"
    assert s["kpis"]["rent_at_risk_open"] == 225000 and s["kpis"]["needs_rereview"] == 1
