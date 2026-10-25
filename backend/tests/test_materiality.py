"""Materiality thresholds flag findings; they never hide or downgrade them."""
from .conftest import WS


def test_thresholds_flag_without_hiding(client):
    did = client.post("/api/sample", headers=WS, json={}).json()["deal"]["id"]
    client.post(f"/api/deals/{did}/audit", headers=WS)
    rows = client.get(f"/api/deals/{did}/findings", headers=WS).json()
    by = {(r["rule_id"], r["subject"].split(" · ")[0]): r for r in rows}
    assert by[("L-001", "Dana Whitfield")]["material"]                 # $2,250/mo ≥ $500
    assert not by[("L-002", "Theo Alvarez")]["material"]               # −$450/mo < $500 — still listed
    assert by[("D-001", "Mina Okafor")]["material_reason"].startswith("deposit")
    s = client.get(f"/api/deals/{did}/summary", headers=WS).json()
    total = s["kpis"]["findings_total"]
    assert s["materiality"]["rent_cents"] == 50000 and 0 < s["kpis"]["material_open"] < total
    r = client.patch(f"/api/deals/{did}", headers=WS, json={"materiality_rent_cents": 0})
    assert r.status_code == 200 and r.json()["materiality_rent_cents"] == 0
    rows2 = client.get(f"/api/deals/{did}/findings", headers=WS).json()
    assert len(rows2) == len(rows) and by[("L-002", "Theo Alvarez")]["id"] in {r["id"] for r in rows2 if r["material"]}
    assert client.patch(f"/api/deals/{did}", headers=WS, json={"materiality_rent_cents": -5}).status_code == 422
