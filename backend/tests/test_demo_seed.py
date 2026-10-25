"""The first-visit demo portfolio: real engine output, replayed on a plausible timeline, seeded exactly once."""
from app import db
from app.engine import metrics
from app.integrate import readiness
from app.main import deal_status

from .conftest import WS


def test_seed_fills_a_new_workspace_once(client):
    r = client.post("/api/demo/seed", headers=WS).json()
    assert r["seeded"] and r["deal_id"]
    deals = client.get("/api/deals", headers=WS).json()
    titles = [d["title"] for d in deals]
    assert titles[0].startswith("Cedar × Bayview") and len(deals) == 3
    assert all(d["is_sample"] for d in deals)  # every demo deal is labelled synthetic
    by = {d["title"].split("|")[0].strip(): d for d in deals}
    with db.connect() as conn:
        states = {}
        for name, d in by.items():
            deal = dict(conn.execute("SELECT * FROM deals WHERE id=?", (d["id"],)).fetchone())
            states[name] = readiness.evaluate(conn, deal, deal_status(conn, deal))["state"]
        bay = by["Cedar × Bayview"]["id"]
        fs = metrics.load_findings(conn, bay)
        events = conn.execute("SELECT MIN(created_at), MAX(created_at) FROM review_events WHERE deal_id=?",
                              (bay,)).fetchone()
        receipt = readiness.latest_receipt(conn, by["Cedar × Lakeshore Living"]["id"])
    assert states == {"Cedar × Bayview": "verifying", "Cedar × Lakeshore Living": "released",
                      "Cedar × Pinecrest Rentals": "planning"}
    statuses = {f["status"] for f in fs}
    assert {"open", "investigating", "expected", "resolved"} <= statuses
    assert events[0] < events[1] and events[0].startswith("2026-10")      # spread over the post-cutover weeks
    assert receipt and receipt["verifies"]
    notes = client.get("/api/intel", headers=WS).json()
    assert len(notes) >= 6 and sum(1 for n in notes if n["linked_deal_id"]) == 3
    assert all((n["website"] or "").endswith(".example") for n in notes)   # reserved, non-resolving domains
    # once per workspace: a second call (or a workspace that already has deals) is a no-op
    again = client.post("/api/demo/seed", headers=WS).json()
    assert again["seeded"] is False and len(client.get("/api/deals", headers=WS).json()) == 3
    for d in deals:
        client.delete(f"/api/deals/{d['id']}", headers=WS)
    assert client.post("/api/demo/seed", headers=WS).json()["seeded"] is False
    # "Try sample acquisition" still means the Bayview sample
    other = {"X-Workspace": "ws_other_seed_02"}
    client.post("/api/demo/seed", headers=other)
    s = client.post("/api/sample", headers=other, json={}).json()
    assert s["created"] is False and s["deal"]["title"].startswith("Cedar × Bayview")
