"""Live KPIs computed from the latest run plus current review state, so every
dashboard number moves when a reviewer acts and never double counts."""
from __future__ import annotations

import json
from collections import defaultdict

from .rules import RULES, SEVERITY_ORDER

OPEN = ("open", "investigating")
CLOSED = ("expected", "resolved")
LEASE_BLOCKING = ("L-001", "L-006")


def load_findings(conn, deal_id: str, include_inactive: bool = False) -> list[dict]:
    q = "SELECT * FROM findings WHERE deal_id=?" + ("" if include_inactive else " AND active=1")
    out = []
    for r in conn.execute(q, (deal_id,)):
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def is_open(f: dict) -> bool:
    """Open, under investigation, or closed but with evidence that changed since the decision."""
    return f["status"] in OPEN or bool(f.get("needs_rereview"))


def _sev_key(f: dict) -> tuple:
    money = f["rent_risk_cents"] + f["deposit_cents"] + f["pending_rent_cents"]
    return (SEVERITY_ORDER.get(f["severity"], 9), -money, f["rule_id"], f["subject"])


def attribute_rent(findings: list[dict], include_closed: bool = False) -> tuple[int, list[dict], dict]:
    """Deduplicate rent at risk: each legacy lease contributes its single largest
    exposure among qualifying findings. Returns (total, lines, counted_by_finding)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        if f["active"] and f["rent_risk_cents"] > 0 and (include_closed or is_open(f)):
            groups[f["risk_key"] or f["id"]].append(f)
    total, lines, counted = 0, [], {}
    for key, fs in sorted(groups.items()):
        fs.sort(key=lambda f: (-f["rent_risk_cents"], f["rule_id"], f["id"]))
        top = fs[0]
        total += top["rent_risk_cents"]
        counted[top["id"]] = {"counted": top["rent_risk_cents"], "covered_by": None}
        for other in fs[1:]:
            counted[other["id"]] = {"counted": 0, "covered_by": top["id"]}
        lines.append({"risk_key": key, "finding_id": top["id"], "rule_id": top["rule_id"], "title": top["title"],
                      "subject": top["subject"], "cents": top["rent_risk_cents"],
                      "also": [{"finding_id": o["id"], "rule_id": o["rule_id"], "title": o["title"],
                                "cents": o["rent_risk_cents"]} for o in fs[1:]]})
    lines.sort(key=lambda l: -l["cents"])
    return total, lines, counted


def summarize(conn, deal: dict, current_fingerprint: str | None) -> dict:
    run = conn.execute("SELECT * FROM audit_runs WHERE deal_id=? AND status='completed' ORDER BY started_at DESC "
                       "LIMIT 1", (deal["id"],)).fetchone()
    if run is None:
        return {"has_run": False}
    summary = json.loads(run["summary_json"])
    findings = load_findings(conn, deal["id"])
    open_f = [f for f in findings if is_open(f)]
    rent_open, rent_lines, counted = attribute_rent(findings)
    rent_detected, _, _ = attribute_rent(findings, include_closed=True)
    dep_open = sum(f["deposit_cents"] for f in open_f)
    dep_detected = sum(f["deposit_cents"] for f in findings)
    pending = sum(f["pending_rent_cents"] for f in open_f)
    sev = {k: 0 for k in SEVERITY_ORDER}
    for f in open_f:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    status_counts = defaultdict(int)
    for f in findings:
        status_counts[f["status"]] += 1

    states = summary["lease_states"]
    active = {k: v for k, v in states.items() if v["active"]}
    by_lease = defaultdict(list)
    for f in findings:
        lk = (f["payload"].get("keys") or {}).get("lease")
        if lk:
            by_lease[lk].append(f)
    matched = accounted = clean = 0
    for k, st in active.items():
        is_matched = st["match"] in ("confirmed", "probable", "reviewer_confirmed")
        fl = by_lease.get(k, [])
        blocking_closed = any(f["rule_id"] in LEASE_BLOCKING and not is_open(f) for f in fl)
        if is_matched:
            matched += 1
        if is_matched or blocking_closed:
            accounted += 1
        if (is_matched or blocking_closed) and not any(is_open(f) for f in fl):
            clean += 1
    n_active = len(active)

    counts = summary["counts"]
    src_total = sum(c["before"] for e, c in counts.items() if e != "deposits")
    verified = sum(c.get("confirmed", 0) + c.get("probable", 0) + c.get("reviewer_confirmed", 0)
                   for e, c in counts.items() if e != "deposits")
    review_open = sum(1 for f in open_f if f["confidence"] == "needs_review")
    attention = sorted(open_f, key=_sev_key)[:3]
    overrides_pending = conn.execute(
        "SELECT COUNT(*) FROM match_overrides WHERE deal_id=? AND created_at>? AND revoked_at IS NULL",
        (deal["id"], run["started_at"])).fetchone()[0]
    revoked_pending = conn.execute(
        "SELECT COUNT(*) FROM match_overrides WHERE deal_id=? AND revoked_at>?",
        (deal["id"], run["started_at"])).fetchone()[0]
    return {
        "has_run": True,
        "run": {"id": run["id"], "started_at": run["started_at"], "finished_at": run["finished_at"],
                "engine_version": run["engine_version"], "fingerprint": run["fingerprint"],
                "stages": json.loads(run["stages_json"]),
                "stale": bool(current_fingerprint and current_fingerprint != run["fingerprint"]),
                "match_decisions_pending": overrides_pending + revoked_pending},
        "labels": summary["labels"], "as_of": summary["as_of"], "counts": counts, "checks": summary["checks"],
        "benign": summary["benign"], "rules": summary["rules"], "skipped": summary["skipped"],
        "files": summary["files"], "totals": summary["totals"], "ai": summary.get("ai"),
        "kpis": {
            "records_total": src_total, "records_verified": verified, "records_needs_review": review_open,
            "critical_open": sev["critical"], "high_open": sev["high"],
            "rent_at_risk_open": rent_open, "rent_at_risk_detected": rent_detected,
            "deposit_open": dep_open, "deposit_detected": dep_detected,
            "deposit_open_count": sum(1 for f in open_f if f["deposit_cents"] > 0),
            "pending_review_rent": pending,
            "findings_total": len(findings), "findings_open": len(open_f),
            "findings_closed": sum(1 for f in findings if not is_open(f)),
            "needs_rereview": sum(1 for f in findings if f["needs_rereview"]),
        },
        "continuity": {
            "active_leases": n_active, "matched": matched, "accounted": accounted, "clean": clean,
            "score": round(accounted / n_active * 100, 1) if n_active else None,
            "clean_pct": round(clean / n_active * 100, 1) if n_active else None,
        },
        "severity_open": sev, "status_counts": dict(status_counts),
        "rent_lines": rent_lines, "rent_counted": counted,
        "attention": [_brief(f, counted) for f in attention],
    }


def _brief(f: dict, counted: dict) -> dict:
    p = f["payload"]
    return {"id": f["id"], "rule_id": f["rule_id"], "title": f["title"], "severity": f["severity"],
            "entity": f["entity"], "subject": f["subject"], "status": f["status"],
            "rent_risk_cents": f["rent_risk_cents"], "deposit_cents": f["deposit_cents"],
            "pending_rent_cents": f["pending_rent_cents"], "explanation": p["explanation"],
            "rent_counted_cents": counted.get(f["id"], {}).get("counted", 0),
            "rent_covered_by": counted.get(f["id"], {}).get("covered_by")}


def finding_row(f: dict, counted: dict) -> dict:
    p = f["payload"]
    src, tgt = p.get("source") or {}, p.get("target") or {}
    return {
        "id": f["id"], "rule_id": f["rule_id"], "rule_name": RULES.get(f["rule_id"], {}).get("name"),
        "title": f["title"], "severity": f["severity"], "entity": f["entity"], "subject": f["subject"],
        "status": f["status"], "resolution": f["resolution"], "active": bool(f["active"]),
        "needs_rereview": bool(f["needs_rereview"]), "confidence": f["confidence"],
        "rent_risk_cents": f["rent_risk_cents"], "deposit_cents": f["deposit_cents"],
        "pending_rent_cents": f["pending_rent_cents"],
        "impact_type": p["impact"].get("type"), "impact_cents": p["impact"].get("cents", 0),
        "rent_counted_cents": counted.get(f["id"], {}).get("counted", 0),
        "rent_covered_by": counted.get(f["id"], {}).get("covered_by"),
        "source_ref": {"file": src.get("file"), "row": src.get("row"), "id": src.get("id")} if src else None,
        "target_ref": {"file": tgt.get("file"), "row": tgt.get("row"), "id": tgt.get("id")} if tgt else None,
        "keys": p.get("keys") or {}, "updated_at": f["updated_at"], "explanation": p["explanation"],
    }


def unit_map(conn, deal_id: str) -> dict:
    run = conn.execute("SELECT summary_json FROM audit_runs WHERE deal_id=? AND status='completed' "
                       "ORDER BY started_at DESC LIMIT 1", (deal_id,)).fetchone()
    if run is None:
        return {"has_run": False, "properties": []}
    summary = json.loads(run["summary_json"])
    findings = load_findings(conn, deal_id)
    by_unit = defaultdict(list)
    for f in findings:
        uk = (f["payload"].get("keys") or {}).get("unit")
        if uk:
            by_unit[uk].append(f)
    states = summary["lease_states"]
    props = []
    for p in summary["unit_map"]:
        units = []
        for u in p["units"]:
            fs = by_unit.get(u["key"], [])
            open_f = sorted([f for f in fs if is_open(f)], key=_sev_key)
            st = states.get(u["lease"]) if u["lease"] else None
            if open_f:
                top = open_f[0]
                if top["rule_id"] in ("L-001", "P-001", "U-001") or (top["severity"] == "critical"):
                    state = "critical"
                elif top["confidence"] == "needs_review":
                    state = "review"
                else:
                    state = "issue"
            elif fs:
                state = "reviewed"
            elif st and st["match"] in ("confirmed", "probable", "reviewer_confirmed"):
                state = "verified"
            elif u["lease"]:
                state = "issue"
            else:
                state = "vacant"
            units.append({**u, "state": state, "findings": [f["id"] for f in open_f] or [f["id"] for f in fs],
                          "top": ({"id": open_f[0]["id"], "title": open_f[0]["title"]} if open_f else None)})
        props.append({**p, "units": units})
    return {"has_run": True, "properties": props}
