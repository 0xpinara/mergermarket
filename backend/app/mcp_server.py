"""MergeProof as a Model Context Protocol server, so AI assistants and coding agents (any MCP client) can run
audits and read evidence through the same deterministic engine the app uses.

Agents can look and re-run; they cannot decide. There is deliberately no tool that resolves, excepts or
confirms a finding: those decisions stay with a named person in the app.

    pip install -r requirements-mcp.txt
    MERGEPROOF_WORKSPACE=ws_my_agent_01 python -m app.mcp_server        # stdio transport

MCP client config:
    {"mcpServers": {"mergeproof": {"command": "/path/to/backend/.venv/bin/python",
                                   "args": ["-m", "app.mcp_server"], "cwd": "/path/to/backend",
                                   "env": {"MERGEPROOF_WORKSPACE": "ws_my_agent_01"}}}}
"""
from __future__ import annotations

import json
import os
import re

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import db, services
from .ai import embeddings
from .engine import metrics, runner

WORKSPACE = os.environ.get("MERGEPROOF_WORKSPACE", "ws_mcp_agent_local")
if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", WORKSPACE):
    raise SystemExit("MERGEPROOF_WORKSPACE must be 8-64 letters, digits, '_' or '-'.")

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
RUN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

server = MCPServer(
    name="mergeproof",
    title="MergeProof",
    version="1.0.0",
    instructions=(
        "MergeProof verifies that tenants, leases, deposits and work orders survived a property-management "
        "migration. Use list_audits or load_sample_acquisition to get a deal_id, run_audit to (re)compute findings, "
        "then get_summary, list_findings and get_evidence. Quote evidence by file and row. Amounts labelled 'rent at "
        "risk' are estimates that require review, not realized losses; deposits are liabilities tracked separately. "
        "You cannot resolve or except findings — tell the user which ones need their decision in the MergeProof app."
    ),
)


def _deal(conn, deal_id: str) -> dict:
    row = conn.execute("SELECT * FROM deals WHERE id=? AND workspace=?", (deal_id, WORKSPACE)).fetchone()
    if row is None:
        raise ValueError(f"No audit {deal_id!r} in workspace {WORKSPACE}. Call list_audits first.")
    return dict(row)


def _status(conn, deal: dict) -> dict:
    from .main import deal_status  # same state rules as the web app
    return deal_status(conn, deal)


@server.tool(annotations=READ)
def list_audits() -> str:
    """List the integration audits in this workspace with their state and last run time."""
    db.init()
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM deals WHERE workspace=? ORDER BY updated_at DESC",
                                              (WORKSPACE,))]
        out = []
        for d in rows:
            st = _status(conn, d)
            out.append({"deal_id": d["id"], "title": d["title"], "state": st["state"],
                        "last_run_at": st["last_run_at"], "open_findings": st["open_findings"],
                        "synthetic_sample": bool(d["is_sample"])})
    return json.dumps(out, indent=1)


@server.tool(annotations=RUN)
def load_sample_acquisition() -> str:
    """Load (or reopen) the synthetic Cedar × Bayview sample deal. All names and amounts are fictional."""
    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM deals WHERE workspace=? AND is_sample=1", (WORKSPACE,)).fetchone()
        did = row["id"] if row else services.load_sample(conn, WORKSPACE)
    return json.dumps({"deal_id": did, "created": row is None})


@server.tool(annotations=RUN)
def run_audit(deal_id: str) -> str:
    """Run (or rerun) the continuity audit. Reruns are safe: findings keep their IDs and review decisions."""
    with db.connect() as conn:
        deal = _deal(conn, deal_id)
        try:
            res = runner.run_audit(conn, deal)
        except runner.AuditBlocked as exc:
            return json.dumps({"ran": False, "problems": exc.problems})
    with db.connect() as conn:
        s = metrics.summarize(conn, _deal(conn, deal_id), res["fingerprint"])
    return json.dumps({"ran": True, "stages": [f"{x['name']}: {x['detail']}" for x in res["stages"]],
                       "kpis": s["kpis"], "continuity": s["continuity"], "ai": s.get("ai")}, indent=1)


@server.tool(annotations=READ)
def get_summary(deal_id: str) -> str:
    """Key results of the latest run: continuity score, rent at risk (estimate), deposits, top findings."""
    with db.connect() as conn:
        deal = _deal(conn, deal_id)
        st = _status(conn, deal)
        s = metrics.summarize(conn, deal, st["fingerprint"])
    if not s.get("has_run"):
        return json.dumps({"has_run": False, "hint": "Call run_audit first."})
    return json.dumps({"state": st["state"], "kpis": s["kpis"], "continuity": s["continuity"],
                       "rent_at_risk_lines": s["rent_lines"], "portfolio_totals": s["checks"],
                       "attention": s["attention"], "ai": s.get("ai"),
                       "note": "rent_at_risk values are monthly estimates in cents that require review"}, indent=1)


@server.tool(annotations=READ)
def list_findings(deal_id: str, status: str = "open", severity: str = "", limit: int = 50) -> str:
    """List findings. status: open | closed | all. severity: critical | high | medium | low (optional)."""
    limit = max(1, min(int(limit), 200))
    with db.connect() as conn:
        _deal(conn, deal_id)
        fs = metrics.load_findings(conn, deal_id)
    _, _, counted = metrics.attribute_rent(fs)
    rows = []
    for f in sorted(fs, key=metrics._sev_key):
        is_open = metrics.is_open(f)
        if (status == "open" and not is_open) or (status == "closed" and is_open):
            continue
        if severity and f["severity"] != severity:
            continue
        r = metrics.finding_row(f, counted)
        rows.append({k: r[k] for k in ("id", "rule_id", "title", "severity", "subject", "status", "confidence",
                                        "rent_counted_cents", "deposit_cents", "pending_rent_cents", "source_ref",
                                        "target_ref")})
    return json.dumps(rows[:limit], indent=1)


@server.tool(annotations=READ)
def get_evidence(finding_id: str) -> str:
    """Full evidence for one finding: explanation, field-by-field comparison with file/row, calculation,
    match reasoning, candidates (AI-suggested ones are labelled) and the review log."""
    with db.connect() as conn:
        row = conn.execute("SELECT f.* FROM findings f JOIN deals d ON d.id=f.deal_id WHERE f.id=? AND "
                           "d.workspace=?", (finding_id, WORKSPACE)).fetchone()
        if row is None:
            raise ValueError(f"No finding {finding_id!r} in this workspace.")
        f = dict(row)
        p = json.loads(f["payload_json"])
        events = [dict(e) for e in conn.execute(
            "SELECT reviewer, action, note, from_status, to_status, created_at FROM review_events WHERE finding_id=? "
            "ORDER BY created_at", (finding_id,))]
    def ref(rec):
        return {"file": rec["file"], "row": rec["row"], "id": rec["id"]} if rec else None
    return json.dumps({
        "id": f["id"], "rule_id": f["rule_id"], "title": f["title"], "severity": f["severity"], "status": f["status"],
        "subject": f["subject"], "explanation": p["explanation"], "recommended_action": p["recommended_action"],
        "legacy_record": ref(p.get("source")), "consolidated_record": ref(p.get("target")),
        "compare": [{"field": c["label"], "legacy": (c.get("before") or {}).get("raw"),
                     "consolidated": (c.get("after") or {}).get("raw"), "state": c["state"]} for c in p["compare"]],
        "calculation": p["impact"].get("calc"), "match": p.get("match"),
        "candidates": [{"record": ref(c.get("record")), "score": c["score"], "reasons": c["reasons"],
                        "origin": c.get("origin", "rules")} for c in p.get("candidates") or []],
        "review_log": events,
    }, indent=1)


@server.tool(annotations=READ)
def ai_status() -> str:
    """Whether local AI assistance is on, which model it uses, and what it is (and isn't) allowed to do."""
    return json.dumps({**embeddings.status(), "suggestion_only": True,
                       "never": ["decides matches", "changes amounts", "sends data off this machine"]})


def main() -> None:
    db.init()
    server.run("stdio")


if __name__ == "__main__":
    main()
