"""Regression gate for eval/integrate_eval.py on a small seed subset. Thresholds come from measured results
(eval/results_integrate.json); where the measurement was perfect the gate is exact."""
import json

from app import db
from app.engine import matching
from app.integrate import plan as planmod
from eval import harness
from eval import integrate_eval as ie

from .conftest import WS


def test_wilson_matches_reference_values():
    w = ie.wilson(0, 10)
    assert (w["lo"], w["hi"]) == (0.0, 0.2775)
    w = ie.wilson(5, 10)
    assert (w["lo"], w["hi"]) == (0.2366, 0.7634)
    assert ie.wilson(0, 0)["p"] is None


def test_typo_that_reorders_tokens_is_still_the_same_name():
    # "valdivia" -> "vladivia" sorts after "vera": token_sort_ratio alone scores 62 and the engine claimed
    # "Lease linked to wrong tenant" on a clean migration (seed 7). Regression for that false claim.
    assert matching.name_similarity("vera valdivia", "vera vladivia") >= 90
    before = harness.load_before()
    after, _ = harness.perturb(7, before, anomalies=False)
    assert not [f for f in harness.run_engine(before, after, None)["findings"] if f["rule_id"] == "L-008"]


def test_clean_migrations_make_no_claims():
    c = ie.clean_cell([1, 7, 1003])
    assert c["claims"] == 0, c["claim_examples"]
    assert c["review_routed"] == 0, c["by_rule"]


def test_corroboration_settles_a_tenant_whose_only_candidate_is_their_own():
    # Seed 7, clean: Keiko Abernathy has a blank email, the same phone and a 93% name match, and holds the lease
    # that matched on unit, dates and rent. Corroboration used to skip her because her own review candidate counts
    # as "claimed", leaving a T-003 review item on a migration with nothing wrong.
    before = harness.load_before()
    after, _ = harness.perturb(7, before, anomalies=False)
    res = harness.run_engine(before, after, None)
    assert not [f for f in res["findings"] if f["rule_id"] == "T-003"]
    st = next(k for k, t in res["S"].tenants.items() if t.n.get("name") == "keiko abernathy")
    assert res["M"]["tenants"].matches[st].status == "probable"


def test_injected_instructions_change_nothing():
    r = ie.injection_cell([1, 1001])
    assert r["invariant"]["k"] == r["invariant"]["n"] == 2, r["differences"]


def test_offsetting_rents_and_blank_vs_zero_deposits():
    o = ie.offsetting_cell([2, 1002])
    assert o["both_caught_while_totals_tie"]["k"] == 2, o["failures"]
    b = ie.blank_zero_cell([3, 1004])
    assert b["all_semantics_hold"]["k"] == 2, b["failures"]


def test_mapping_suggester_never_auto_suggests_a_wrong_column():
    m = ie.mapping_cell([1, 2, 1001], use_ai=False)
    assert m["unsafe_auto_suggestions"]["k"] == 0, m["unsafe_examples"]
    assert m["decoys"]["safe"]["k"] == m["decoys"]["safe"]["n"]
    assert m["decoys"]["decoy_auto_suggested"] == 0
    assert m["absent_column_not_auto_filled"]["k"] == m["absent_column_not_auto_filled"]["n"]
    assert m["auto_coverage"]["p"] >= 0.85


def test_dry_run_blocks_structural_mapping_mistakes():
    d = ie.dry_run_cell([1])
    assert d["baseline_clean"]["k"] == 1, d["baselines_not_clean"]
    must_block = ["unit key from the door-code column", "base rent from market rent",
                  "lease status: active mapped to Past", "lease status: expired mapped to Current",
                  "resident reference from the unit column", "work-order status: open mapped to Closed",
                  "deposit held from the required-deposit column", "unit number from the door code",
                  "resident email from the phone column"]
    for name in must_block:
        assert d["by_mistake"][name]["gate_blocking"]["k"] == 1, name
    for name in ("move-in date from the signed date", "move-in date from the end date",
                 "move-in date written DD/MM/YYYY"):
        assert d["by_mistake"][name]["surfaced"]["k"] == 1, name  # visible as predicted findings even if not gated


def test_export_locks_again_after_any_change():
    s = ie.stale_cell()
    assert s["export_allowed_when_signed_and_current"]
    assert s["all_refused"], s["refused"]


def test_dry_run_refuses_while_legacy_mapping_is_incomplete(client):
    did = client.post("/api/sample", headers=WS, json={}).json()["deal"]["id"]
    client.post(f"/api/deals/{did}/integrate/plan", headers=WS, json={"template_id": "cedar-import-v1",
                                                                     "reviewer": "Ana"})
    with db.connect() as conn:
        sid, m = conn.execute("SELECT id, mapping_json FROM snapshots WHERE deal_id=? AND side='before' AND "
                              "entity='tenants'", (did,)).fetchone()
        m = json.loads(m)
        m.pop("tenant_id")
        conn.execute("UPDATE snapshots SET mapping_json=? WHERE id=?", (db.dumps(m), sid))
        assert ie.audit_mapping_complete(conn, did) is False
    r = client.post(f"/api/deals/{did}/integrate/dry-run", headers=WS, json={"reviewer": "Ana"})
    assert r.status_code == 409 and "Finish mapping the legacy exports" in r.json()["detail"]
    assert "Tenant ID" in r.json()["detail"]


def test_variant_ground_truth_is_consistent():
    tables, truth, info = ie.legacy_variant(5, all_decoys=True, drops=False)
    headers = {e: set(h) for e, (_, h, _) in tables.items()}
    for iid, t in truth.items():
        if isinstance(t, list):
            ent = ie.TRUTH[iid][0]
            assert set(t) <= headers[ent], iid
    assert set(info["decoys"]) == {"unit_code", "alt_phone", "market_rent", "signed_date", "deposit_required",
                                   "cost", "vendor"}
    # the decoy door code is unique and unreferenced: a key candidate nothing points at
    with ie.temp_db(), ie.ai_mode(False):
        with db.connect() as conn:
            deal = ie.load_variant(conn, tables)
            prof = planmod.sources_for(conn, deal["id"]).profile
    units = next(f for f in prof["files"] if f["entity"] == "units")
    assert info["decoys"]["unit_code"] in units["keys"]
    assert not [r for r in prof["relationships"] if r["to"]["column"] == info["decoys"]["unit_code"]]
