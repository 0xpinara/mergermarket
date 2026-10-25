"""D-005: a blank deposit balance is unknown, never zero."""
import json

from app import db, services
from app.engine import metrics, runner


def _set_balance(conn, did, side, entity, row, column, value):
    sid = conn.execute("SELECT id FROM snapshots WHERE deal_id=? AND side=? AND entity=?", (did, side, entity)).fetchone()[0]
    raw = json.loads(conn.execute("SELECT raw_json FROM source_rows WHERE snapshot_id=? AND row_number=?",
                                  (sid, row)).fetchone()[0])
    old = raw[column]
    raw[column] = value
    conn.execute("UPDATE source_rows SET raw_json=? WHERE snapshot_id=? AND row_number=?", (db.dumps(raw), sid, row))
    return old


def test_blank_balance_is_flagged_not_netted(tmpdb):
    with db.connect() as conn:
        did = services.load_sample(conn, "ws_test_000001")
        # Consolidated side: first deposit (lease BV-L-1002 → CPM-L-50002, $2,000) recorded with a blank balance.
        _set_balance(conn, did, "after", "deposits", 2, "Deposit Held", "")
        # Legacy side: a different lease's balance is blank (unknown), while Cedar still has an amount.
        _set_balance(conn, did, "before", "deposits", 3, "deposit_balance", "")
        deal = dict(conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        runner.run_audit(conn, deal)
        fs = metrics.load_findings(conn, did)
    d5 = [f for f in fs if f["rule_id"] == "D-005"]
    assert len(d5) == 2
    target_blank = next(f for f in d5 if not f["payload"]["anchor"].startswith("D-005|before:"))
    legacy_blank = next(f for f in d5 if f["payload"]["anchor"].startswith("D-005|before:"))
    assert target_blank["severity"] == "high" and target_blank["deposit_cents"] == 200000
    assert "Blank is not zero" in target_blank["payload"]["explanation"]
    assert legacy_blank["severity"] == "medium" and legacy_blank["deposit_cents"] == 0
    # neither lease is also reported as a balance difference or an unexplained new deposit
    leases = {f["payload"]["keys"].get("lease") for f in d5}
    assert not [f for f in fs if f["rule_id"] in ("D-002", "D-003") and f["payload"]["keys"].get("lease") in leases]


def test_zero_balance_is_a_real_difference(tmpdb):
    with db.connect() as conn:
        did = services.load_sample(conn, "ws_test_000001")
        _set_balance(conn, did, "after", "deposits", 2, "Deposit Held", "$0.00")
        deal = dict(conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        runner.run_audit(conn, deal)
        fs = metrics.load_findings(conn, did)
    d2 = [f for f in fs if f["rule_id"] == "D-002" and f["deposit_cents"] == 200000]
    assert len(d2) == 1 and not [f for f in fs if f["rule_id"] == "D-005"]
