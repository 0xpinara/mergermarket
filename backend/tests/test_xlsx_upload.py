"""XLSX workbooks go through the same upload → mapping → audit path as CSV."""
import csv
import io

from openpyxl import Workbook

from app import config

from .conftest import WS


def _workbook(side: str) -> bytes:
    """One sheet per sample export, so a single .xlsx carries a whole side."""
    wb = Workbook()
    wb.remove(wb.active)
    for path in sorted((config.SAMPLE_DIR / side).glob("*.csv")):
        ws = wb.create_sheet(path.stem[:31])
        for row in csv.reader(io.StringIO(path.read_text(encoding="utf-8"))):
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_multisheet_xlsx_audit_matches_csv_results(client):
    did = client.post("/api/deals", headers=WS, json={"title": "XLSX", "acquirer": "Cedar Property Management",
                                                      "target_business": "Bayview Rentals",
                                                      "deal_date": "2026-09-30"}).json()["id"]
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    for side in ("before", "after"):
        r = client.post(f"/api/deals/{did}/snapshots", headers=WS, data={"side": side, "confirm_synthetic": "true"},
                        files=[("files", (f"{side}.xlsx", _workbook(side), xlsx))])
        assert r.status_code == 200, r.text
        created = r.json()["created"]
        assert len(created) == 6
        assert {s["entity"] for s in created} == {"properties", "units", "tenants", "leases", "deposits", "maintenance"}
        assert all(s["validation"]["status"] == "ready" for s in created)
    res = client.post(f"/api/deals/{did}/audit", headers=WS)
    assert res.status_code == 200
    k = res.json()["summary"]["kpis"]
    assert k["rent_at_risk_open"] == 442500 and k["deposit_open"] == 414000
    fs = client.get(f"/api/deals/{did}/findings", headers=WS).json()
    missing = next(f for f in fs if f["rule_id"] == "L-001")
    assert missing["source_ref"]["file"] == "before.xlsx › bayview_leases" and missing["source_ref"]["row"] == 43
