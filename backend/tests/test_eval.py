"""Ground-truth evaluation as a regression gate (rules only, so it runs offline and deterministically)."""
from eval import harness


def test_engine_scorecard_on_randomized_migrations():
    r = harness.evaluate(seeds=3, use_ai=False)
    a = r["anomalies"]
    assert a["injected"] > 50
    assert a["false_alarms"] == 0, a["false_alarm_examples"]   # no wrong claims
    assert a["silently_missed"] == 0, a["missed_examples"]     # anything not found is at least flagged for review
    assert a["recall"] >= 0.97
    assert r["money"]["rent_at_risk_exact"] == 3 and r["money"]["deposit_exact"] == 3
    assert r["mapping_failures"] == 0
    for ent, m in r["matching"].items():
        assert m["precision"] == 1.0, ent                       # never pairs the wrong records
        assert m["recall"] >= 0.98, ent


def test_perturbation_is_deterministic():
    before = harness.load_before()
    a1, t1 = harness.perturb(7, before)
    a2, t2 = harness.perturb(7, before)
    assert a1 == a2 and t1["expected"] == t2["expected"] and t1["rent_risk"] == t2["rent_risk"]


def test_header_benchmark_rules_never_guess_wrong():
    h = harness.header_bench(None)["summary"]
    assert h["wrong"] == 0 and h["correct"] >= 15
