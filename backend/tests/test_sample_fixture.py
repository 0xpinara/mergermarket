"""Deterministic fixture test: the seeded Cedar x Bayview anomalies are each
detected with the intended values, and nothing benign raises an alarm."""
import json

from app import db, services
from app.engine import metrics, runner

from .conftest import by_rule, by_subject


def payload(f):
    return f["payload"]


def compare(f, field):
    return next(r for r in payload(f)["compare"] if r["field"] == field)


def test_missing_active_lease(sample_run):
    _, fs, _ = sample_run
    [f] = by_rule(fs, "L-001")
    assert f["severity"] == "critical"
    assert "Dana Whitfield" in f["subject"] and "4B" in f["subject"]
    assert f["rent_risk_cents"] == 225000
    src = payload(f)["source"]
    assert src["file"] == "bayview_leases.csv" and src["row"] == 43 and src["id"] == "BV-L-1042"
    assert payload(f)["target"] is None
    assert "$2,250.00/month" in payload(f)["explanation"]
    assert "with no lease attached" in payload(f)["explanation"]


def test_rent_decrease_450(sample_run):
    _, fs, _ = sample_run
    [f] = [x for x in by_rule(fs, "L-002") if "Theo Alvarez" in x["subject"]]
    row = compare(f, "monthly_rent")
    assert row["before"]["value"] == "$2,500.00" and row["after"]["value"] == "$2,050.00"
    assert row["state"] == "differs"
    assert f["rent_risk_cents"] == 45000
    # everything else about the lease is the same or format-only
    states = {r["field"]: r["state"] for r in payload(f)["compare"]}
    assert states["lease_id"] == "rekeyed" and states["start_date"] == "format" and states["unit"] == "same"


def test_deposit_missing_is_liability_not_rent(sample_run):
    _, fs, summary = sample_run
    mina = [f for f in by_rule(fs, "D-001") if "Mina Okafor" in f["subject"]]
    assert len(mina) == 1
    f = mina[0]
    assert f["deposit_cents"] == 180000 and f["rent_risk_cents"] == 0
    assert payload(f)["impact"]["type"] == "deposit"
    assert summary["kpis"]["deposit_open"] == 180000 + 225000 + 9000
    # deposits never feed the rent metric
    assert summary["kpis"]["rent_at_risk_open"] == sum(l["cents"] for l in summary["rent_lines"])
    assert all(by_rule(fs, r)[0]["rent_risk_cents"] == 0 for r in ("D-001", "D-002"))


def test_duplicate_tenant(sample_run):
    _, fs, _ = sample_run
    [f] = by_rule(fs, "T-001")
    assert "Priya Raman" in f["subject"]
    ids = {payload(f)["target"]["id"]} | {x["id"] for x in payload(f)["extra_targets"]}
    assert len(ids) == 2
    # the duplicate does not break lease matching (no wrong-tenant finding for her lease)
    assert not [x for x in by_rule(fs, "L-008") if "Priya" in x["subject"]]


def test_missing_open_urgent_ticket(sample_run):
    _, fs, _ = sample_run
    [f] = [x for x in by_rule(fs, "M-001") if "No heat" in x["subject"]]
    assert f["severity"] == "critical"
    assert "No heat" in f["subject"]
    assert payload(f)["source"]["file"] == "bayview_work_orders.csv"


def test_broken_unit_references(sample_run):
    _, fs, _ = sample_run
    [orphan] = by_rule(fs, "L-009")
    assert "Grace Liu" in orphan["subject"]
    assert "CPM-U-29999" in payload(orphan)["explanation"]
    [wrong] = by_rule(fs, "L-007")
    assert "Jun Park" in wrong["subject"] and "1D" in payload(wrong)["explanation"]


def test_ambiguous_match_requires_review(sample_run):
    _, fs, summary = sample_run
    alex = by_subject(fs, "Alex Kim")
    assert [f["rule_id"] for f in alex] == ["L-006"]
    f = alex[0]
    assert f["confidence"] == "needs_review" and f["rent_risk_cents"] == 0
    assert f["pending_rent_cents"] == 187500
    cands = payload(f)["candidates"]
    assert len(cands) == 2 and all(c["score"] < 60 for c in cands)
    assert summary["kpis"]["pending_review_rent"] == 187500
    # never reported as missing
    assert not [x for x in by_rule(fs, "L-001") if "Alex" in x["subject"]]


def test_benign_rekey_and_address_format_do_not_alarm(sample_run):
    _, fs, summary = sample_run
    marigold = [f for f in by_subject(fs, "Marigold")]
    assert [f["rule_id"] for f in marigold] == ["L-011"]  # only the genuinely new post-cutover lease
    assert marigold[0]["severity"] == "low"
    counts = summary["counts"]
    assert counts["properties"]["confirmed"] == 8 and counts["units"]["confirmed"] == 186
    assert summary["benign"]["Address abbreviations"]["count"] == 1
    assert summary["benign"]["Unit label format"]["count"] == 30
    assert summary["benign"]["IDs re-keyed"]["count"] > 600


def test_rent_at_risk_is_deduplicated(sample_run):
    _, fs, summary = sample_run
    omar = by_subject(fs, "Omar Haddad")
    assert sorted(f["rule_id"] for f in omar) == ["L-002", "L-004"]
    assert {f["risk_key"] for f in omar} == {omar[0]["risk_key"]}
    # $2,250 missing + $450 lower rent + $1,725 inactive (its $150 rent cut is NOT added)
    assert summary["kpis"]["rent_at_risk_open"] == 225000 + 45000 + 172500
    line = next(l for l in summary["rent_lines"] if "Omar" in l["subject"])
    assert line["cents"] == 172500 and line["also"][0]["cents"] == 15000


def test_rent_increase_not_at_risk(sample_run):
    _, fs, _ = sample_run
    [f] = by_rule(fs, "L-003")
    assert f["rent_risk_cents"] == 0 and payload(f)["impact"]["cents"] == 14500


def test_aggregates_hide_row_level_gaps(sample_run):
    _, _, summary = sample_run
    agg = {c["id"]: c for c in summary["checks"]}
    assert agg["AGG-1"]["before"] == agg["AGG-1"]["after"] == 175
    assert agg["AGG-1"]["status"] == "warn"
    assert agg["AGG-2"]["delta"] == -20500  # only -$205 at portfolio level, vs $4,425 row-level


def test_expected_finding_set_is_exact(sample_run):
    _, fs, _ = sample_run
    got = sorted(f["rule_id"] for f in fs)
    assert got == sorted(["L-001", "L-002", "L-002", "L-003", "L-004", "L-005", "L-006", "L-007", "L-009", "L-011",
                          "L-012", "T-001", "D-001", "D-001", "D-002", "M-001", "M-001", "M-002", "M-004"])


def test_rewritten_work_order_is_missing_for_rules_alone(sample_run):
    _, fs, _ = sample_run
    [mold] = [x for x in by_rule(fs, "M-001") if "Mold" in x["subject"]]
    assert mold["severity"] == "high" and mold["confidence"] == "unmatched"  # AI off: no suggestion


def test_continuity_score(sample_run):
    _, _, summary = sample_run
    c = summary["continuity"]
    assert c["active_leases"] == 175 and c["matched"] == 173 and c["accounted"] == 173
    assert c["score"] == round(173 / 175 * 100, 1)


def test_rerun_is_idempotent_and_deterministic(sample_run):
    did, fs, _ = sample_run
    with db.connect() as conn:
        deal = dict(conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        runner.run_audit(conn, deal)
    with db.connect() as conn:
        fs2 = metrics.load_findings(conn, did, include_inactive=True)
    assert sorted(f["id"] for f in fs) == sorted(f["id"] for f in fs2)
    assert {f["id"]: f["evidence_hash"] for f in fs} == {f["id"]: f["evidence_hash"] for f in fs2}
    # a second, independent sample deal produces the same findings (ids differ only by deal scope)
    with db.connect() as conn:
        other = services.load_sample(conn, "ws_test_000002")
        runner.run_audit(conn, dict(conn.execute("SELECT * FROM deals WHERE id=?", (other,)).fetchone()))
        fs3 = metrics.load_findings(conn, other)
    def sig(x):
        return sorted((f["rule_id"], f["subject"], f["rent_risk_cents"], f["deposit_cents"], f["evidence_hash"])
                      for f in x)
    assert sig(fs) == sig(fs3)


def test_provenance_kept(sample_run):
    _, fs, _ = sample_run
    f = by_rule(fs, "D-002")[0]
    src, tgt = payload(f)["source"], payload(f)["target"]
    assert src["raw"]["deposit_balance"] == "1650.00"
    assert tgt["raw"]["Deposit Held"] == "$1,560.00"
    assert src["row"] > 1 and tgt["row"] > 1
    json.dumps(payload(f))
