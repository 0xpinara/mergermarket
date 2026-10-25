"""The MCP server exposes the same engine to AI agents — read and rerun only, never decide."""
import asyncio
import json

import pytest

mcp = pytest.importorskip("mcp")


@pytest.fixture()
def server(tmpdb, monkeypatch):
    monkeypatch.setenv("MERGEPROOF_WORKSPACE", "ws_mcp_test_01")
    import importlib

    import app.mcp_server as mod
    mod = importlib.reload(mod)
    return mod


def _text(result) -> dict | list:
    return json.loads(result.content[0].text)


def test_tools_are_read_or_rerun_only(server):
    tools = asyncio.run(server.server.list_tools())
    names = {t.name for t in tools}
    assert {"list_audits", "load_sample_acquisition", "run_audit", "get_summary", "list_findings",
            "get_evidence", "ai_status"} <= names
    assert not any(w in n for n in names for w in ("review", "resolve", "except", "confirm", "delete"))
    assert all(t.annotations and t.annotations.destructive_hint is False for t in tools)
    reads = {t.name for t in tools if t.annotations.read_only_hint}
    assert reads == {"list_audits", "get_summary", "list_findings", "get_evidence", "ai_status"}


def test_agent_can_audit_and_quote_evidence(server):
    call = lambda name, **kw: _text(asyncio.run(server.server.call_tool(name, kw)))  # noqa: E731
    deal = call("load_sample_acquisition")["deal_id"]
    ran = call("run_audit", deal_id=deal)
    assert ran["ran"] and ran["kpis"]["rent_at_risk_open"] == 442500
    crit = call("list_findings", deal_id=deal, severity="critical")
    missing = next(f for f in crit if f["rule_id"] == "L-001")
    ev = call("get_evidence", finding_id=missing["id"])
    assert ev["legacy_record"] == {"file": "bayview_leases.csv", "row": 43, "id": "BV-L-1042"}
    assert ev["consolidated_record"] is None and ev["calculation"][-1][1] == "$2,250.00"
    assert call("get_summary", deal_id=deal)["continuity"]["active_leases"] == 175
    assert [a["deal_id"] for a in call("list_audits")] == [deal]
