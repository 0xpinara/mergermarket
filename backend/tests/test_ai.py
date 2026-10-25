"""AI assistance: suggestion-only, explainable, never changes money, degrades to rules when unavailable."""
import hashlib
import os
import re

import numpy as np
import pytest

from app.ai import assist, embeddings
from app.ingest import suggest_mapping

from .conftest import WS


class FakeEmbedder:
    """Deterministic bag-of-stems vectors: stands in for the local model so tests run offline."""
    model_id = "fake/bag-of-words"
    dim = 8192

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z]+", t.lower()):
                w = {"mould": "mold"}.get(w, w).rstrip("s")
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(n == 0, 1, n)

    def similarity(self, a, b):
        return self.encode(a) @ self.encode(b).T


@pytest.fixture()
def fake_ai(monkeypatch):
    monkeypatch.setenv("MERGEPROOF_AI", "on")
    embeddings.reset_for_tests(FakeEmbedder(), tried=True)
    yield
    embeddings.reset_for_tests()


def test_header_suggestions_are_suggestions_only():
    headers = ["Job Number", "Issue Text", "Urgency Level", "Work Status", "Problem Reported"]
    rules_only = {k: v["column"] for k, v in suggest_mapping("maintenance", headers).items()}
    sugg = assist.header_suggestions(FakeEmbedder(), "maintenance", headers, rules_only)
    assert sugg["summary"]["column"] == "Issue Text" and sugg["summary"]["origin"] == "ai"
    # "Problem Reported" is equally close to two fields here, so the margin rule declines to guess
    assert "Problem Reported" not in {s["column"] for s in sugg.values()}
    assert all(s["column"] not in rules_only.values() for s in sugg.values())  # never re-uses a mapped column
    assert assist.header_suggestions(None, "maintenance", headers, rules_only) == {}  # unavailable -> nothing


def test_stemmed_alias_rule_maps_without_ai():
    m = suggest_mapping("leases", ["Agreement No", "Resident Moved In", "Base Rent", "Unit"])
    assert m["start_date"]["column"] == "Resident Moved In" and "alias words" in m["start_date"]["method"]


def _audit(client):
    did = client.post("/api/sample", headers=WS, json={}).json()["deal"]["id"]
    r = client.post(f"/api/deals/{did}/audit", headers=WS)
    assert r.status_code == 200
    return did, r.json()["summary"]


def test_ai_suggests_rewritten_work_order_and_human_confirms(client, fake_ai):
    did, s = _audit(client)
    assert s["run"]["stages"][3]["detail"].endswith("1 AI suggestion")
    fs = client.get(f"/api/deals/{did}/findings", headers=WS).json()
    mold = next(f for f in fs if f["rule_id"] == "M-001" and "Mold" in f["subject"])
    heat = next(f for f in fs if f["rule_id"] == "M-001" and "No heat" in f["subject"])
    assert mold["confidence"] == "needs_review" and heat["confidence"] == "unmatched"
    d = client.get(f"/api/findings/{mold['id']}", headers=WS).json()
    [cand] = d["payload"]["candidates"]
    assert cand["origin"] == "ai" and cand["record"]["id"] == "CPM-WO-90064"
    assert all(points == 0 for _, points in cand["reasons"])  # AI adds no auto-match points
    assert "suggestion only" in d["payload"]["explanation"]
    r = client.post(f"/api/findings/{mold['id']}/review", headers=WS,
                    json={"action": "confirm_match", "reviewer": "Ops", "candidate": cand["target_key"]})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    s2 = client.post(f"/api/deals/{did}/audit", headers=WS).json()["summary"]
    assert s2["counts"]["maintenance"]["reviewer_confirmed"] == 1
    assert not [f for f in client.get(f"/api/deals/{did}/findings", headers=WS).json() if "Mold" in f["subject"]]


def test_ai_never_changes_money_or_continuity(client, monkeypatch):
    _, off = _audit(client)
    monkeypatch.setenv("MERGEPROOF_AI", "on")
    embeddings.reset_for_tests(FakeEmbedder(), tried=True)
    r = client.post("/api/sample", headers=WS, json={"reset": True}).json()
    on = client.post(f"/api/deals/{r['deal']['id']}/audit", headers=WS).json()["summary"]
    for k in ("rent_at_risk_open", "deposit_open", "pending_review_rent", "critical_open", "findings_total"):
        assert on["kpis"][k] == off["kpis"][k], k
    assert on["continuity"] == off["continuity"]
    assert on["kpis"]["records_needs_review"] == off["kpis"]["records_needs_review"] + 1


def test_ai_unavailable_falls_back_to_rules(client, monkeypatch):
    monkeypatch.setenv("MERGEPROOF_AI", "on")
    monkeypatch.setenv("MERGEPROOF_EMBED_MODEL", "/nonexistent/model-path")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    embeddings.reset_for_tests()
    did, s = _audit(client)
    run = client.get(f"/api/deals/{did}/report", headers=WS).json()["summary"]
    assert s["kpis"]["rent_at_risk_open"] == 442500
    card = client.get("/api/ai").json()
    assert card["status"]["enabled"] and not card["status"]["available"] and card["status"]["reason"]
    assert run["has_run"]


def test_ai_card_describes_limits(client):
    card = client.get("/api/ai").json()
    assert card["status"]["enabled"] is False and card["off_switch"] == "MERGEPROOF_AI=off"
    assert any("rent at risk" in n for n in card["never"])


def _real_model():
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        return embeddings.LocalEmbedder(embeddings.DEFAULT_MODEL)
    except Exception:
        return None
    finally:
        os.environ.pop("HF_HUB_OFFLINE", None)


@pytest.mark.skipif(os.environ.get("MERGEPROOF_SKIP_REAL_AI") == "1", reason="real-model test disabled")
def test_real_local_model_when_cached():
    emb = _real_model()
    if emb is None:
        pytest.skip("local model not cached on this machine")
    hits = assist.ticket_suggestions(
        emb, {"text": "Mold spotting on bathroom ceiling", "opened": "2026-09-18"},
        [{"key": "a", "text": "Mould on bathroom ceiling above shower, tenant requests inspection",
          "opened": "2026-09-22", "same_property": True},
         {"key": "b", "text": "Ceiling stain after rain", "opened": "2026-09-20", "same_property": False},
         {"key": "c", "text": "Garbage disposal jammed", "opened": "2026-09-19", "same_property": True}])
    assert hits and hits[0]["key"] == "a"
    assert "b" not in {h["key"] for h in hits}  # other property is filtered out before any similarity is used
    sugg = assist.header_suggestions(emb, "maintenance", ["Job Number", "Problem Reported"], {})
    assert sugg["summary"]["column"] == "Problem Reported"
