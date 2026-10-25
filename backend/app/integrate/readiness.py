"""Readiness gates, reviewer sign-off, the prediction-vs-observation comparison and the release receipt.

Gates are recomputed from stored state on every read — nothing is cached as "passed". Sign-off is a single named
reviewer acknowledging one exact plan hash and dry run; it is labelled as such everywhere, and browser workspace
IDs are not identity. The receipt records which checks ran and passed against which inputs; it is not an audit
opinion and MergeProof never certifies anything."""
from __future__ import annotations

import hashlib
import json

from .. import config, db
from ..engine import metrics
from . import dryrun
from . import plan as planmod

TIE_IDS = ("AGG-1", "AGG-2", "AGG-3", "AGG-4", "AGG-5")
SIGNOFF_TEXT = ("I reviewed integration plan {hash} (version {version}) and dry run {dry}: {accepted} rows simulated, "
                "{rejected} rejected, legacy → simulated tie-outs exact, {crit} predicted findings at medium or above. "
                "I approve exporting these import files for loading. Single-reviewer sign-off — not an audit opinion.")
LIMITS = [
    "Single-reviewer sign-off. Browser workspace IDs are not identity or tenant authorization.",
    "Dry-run results model a strict importer; the destination's real importer behaviour is an assumption.",
    "Rent at risk is a monthly estimate that needs review, not a realized loss. Deposits are liabilities, "
    "tracked separately.",
    "This receipt records which checks ran and passed on which inputs. It is not an audit opinion or certification.",
]


def latest_signoff(conn, deal_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM signoffs WHERE deal_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                     (deal_id,)).fetchone()
    return dict(r) if r else None


def latest_run(conn, deal_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM audit_runs WHERE deal_id=? AND status='completed' ORDER BY started_at DESC LIMIT 1",
                     (deal_id,)).fetchone()
    return dict(r) if r else None


def _gate(gid: str, phase: str, label: str, ok: bool | None, detail: str) -> dict:
    return {"id": gid, "phase": phase, "label": label,
            "status": "pending" if ok is None else ("pass" if ok else "fail"), "detail": detail}


def evaluate(conn, deal: dict, deal_status: dict | None = None) -> dict:
    p = planmod.load(conn, deal["id"])
    if p is None:
        return {"has_plan": False, "gates": [], "signoff_ready": False, "export_ready": False,
                "release_ready": False, "state": "no_plan"}
    d = planmod.drift(conn, p)
    c = planmod.counts(p)
    dr = dryrun.latest(conn, deal["id"])
    so = latest_signoff(conn, deal["id"])
    so_live = so if so and not so["revoked_at"] else None
    g = []
    g.append(_gate("G1", "plan", "Legacy exports unchanged since the plan was built", not d["changed"],
                   "Source contract matches." if not d["changed"] else
                   f"The legacy exports changed ({len(d['issues'])} column issue(s)). Review and rebase the plan."))
    undecided = c["total"] - c["decided"]
    g.append(_gate("G2", "plan", "Every target column approved or deliberately ignored", undecided == 0,
                   "All columns decided." if undecided == 0 else
                   f"{undecided} column(s) still need a decision ({c['required_open']} required)."))
    g.append(_gate("G3", "plan", "Financial mappings approved one by one", c["financial_pending"] == 0,
                   "Rent, deposit and lease-status mappings individually approved." if c["financial_pending"] == 0
                   else f"{c['financial_pending']} financial column(s) awaiting individual approval."))
    current = bool(dr and dr["plan_hash"] == p["plan_hash"] and dr["contract_fp"] == p["contract_fp"]
                   and not d["changed"])
    g.append(_gate("G4", "simulate", "Dry run is current for this exact plan", current,
                   "No dry run yet." if not dr else ("Dry run matches plan " + p["plan_hash"][:12] + "." if current else
                                                     "The plan or its sources changed after the last dry run.")))
    if current:
        rej = dr["totals"]["rejected"]
        g.append(_gate("G5", "simulate", "Preflight: no row rejected by import constraints", rej == 0,
                       "Every simulated row passed." if rej == 0 else
                       f"{rej} row(s) would be rejected — see blast radius."))
        ties = {t["id"]: t for t in dr["tieouts"]}
        bad = [ties[i]["label"] for i in TIE_IDS if i in ties and ties[i]["status"] != "pass"]
        bad_props = [p_["property"] for p_ in dr["properties"] if not p_["ties"]]
        if bad_props:
            bad.append(f"active rent by property ({len(bad_props)}: {', '.join(bad_props[:3])}"
                       f"{'…' if len(bad_props) > 3 else ''})")
        g.append(_gate("G6", "simulate", "Counts and money tie out legacy → simulated to the cent", not bad,
                       "Active leases, rent (portfolio and per property), deposits and open work orders tie out."
                       if not bad else "Doesn't tie: " + ", ".join(bad) + "."))
        pk = dr["predicted_kpis"]
        hc = pk["critical"] + pk["high"] + pk["medium"]
        g.append(_gate("G7", "simulate", "Simulated verification predicts no critical, high or medium findings",
                       hc == 0,
                       f"{pk['findings']} predicted finding(s), all low/informational." if hc == 0 else
                       f"{hc} predicted finding(s) at medium or above ({pk['critical']} critical, {pk['high']} high, "
                       f"{pk['medium']} medium)."))
    else:
        for gid, label in (("G5", "Preflight: no row rejected by import constraints"),
                           ("G6", "Counts and money tie out legacy → simulated to the cent"),
                           ("G7", "Simulated verification predicts no critical, high or medium findings")):
            g.append(_gate(gid, "simulate", label, None, "Run a dry run on the current plan."))
    signed = bool(so_live and dr and so_live["plan_hash"] == p["plan_hash"] and so_live["dry_run_id"] == dr["id"])
    g.append(_gate("G8", "approve", "Named reviewer signed off this plan and dry run", signed,
                   f"Signed by {so_live['reviewer']} (single reviewer)." if signed else
                   ("Sign-off is for an earlier plan or dry run." if so_live else "Not signed off.")))
    run = latest_run(conn, deal["id"])
    stale = bool(deal_status and run and deal_status.get("fingerprint") != run["fingerprint"])
    after_signoff = bool(run and so_live and run["started_at"] >= so_live["created_at"])
    verified = bool(run and not stale and after_signoff and signed)
    g.append(_gate("G9", "verify", "Post-import verification ran on the actual target export, after sign-off",
                   verified if signed else None,
                   "Verification is current." if verified else
                   ("Sign off and export first." if not signed else
                    ("No verification run yet." if not run else
                     ("Exports changed since the last verification — rerun it." if stale else
                      "The last verification ran before sign-off — rerun it on the imported data.")))))
    if verified:
        fs = metrics.load_findings(conn, deal["id"])
        open_hc = [f for f in fs if metrics.is_open(f) and f["severity"] in ("critical", "high")]
        g.append(_gate("G10", "verify", "No open critical or high findings after import", not open_hc,
                       "All critical/high findings reviewed." if not open_hc else
                       f"{len(open_hc)} critical/high finding(s) still open."))
        money_open = [f for f in fs if metrics.is_open(f) and (f["rent_risk_cents"] or f["deposit_cents"])]
        g.append(_gate("G11", "verify", "Every rent and deposit difference after import is explained", not money_open,
                       "Money differences reviewed or none found." if not money_open else
                       f"{len(money_open)} rent/deposit finding(s) still open."))
    else:
        g.append(_gate("G10", "verify", "No open critical or high findings after import", None, "Verify first."))
        g.append(_gate("G11", "verify", "Every rent and deposit difference after import is explained", None,
                       "Verify first."))
    passed = {x["id"] for x in g if x["status"] == "pass"}
    signoff_ready = all(f"G{i}" in passed for i in range(1, 8))
    export_ready = signoff_ready and "G8" in passed
    release_ready = export_ready and all(f"G{i}" in passed for i in (9, 10, 11))
    state = ("released" if release_ready and _receipt_current(conn, deal["id"], p) else
             "ready_to_release" if release_ready else "verifying" if export_ready else
             "ready_to_sign" if signoff_ready else "simulating" if all(f"G{i}" in passed for i in (1, 2, 3)) else
             "planning")
    return {"has_plan": True, "gates": g, "signoff_ready": signoff_ready, "export_ready": export_ready,
            "release_ready": release_ready, "state": state, "plan_hash": p["plan_hash"], "plan_version": p["version"],
            "dry_run_id": dr["id"] if dr else None, "signoff": so, "drift": d}


def sign(conn, deal: dict, reviewer: str, acknowledge: bool, deal_status: dict | None = None) -> dict:
    if not acknowledge:
        raise planmod.PlanError("Tick the acknowledgement to sign off.", 422)
    r = evaluate(conn, deal, deal_status)
    if not r["signoff_ready"]:
        failing = [f"{x['id']} {x['label']}" for x in r["gates"] if x["phase"] != "approve"
                   and x["phase"] != "verify" and x["status"] != "pass"]
        raise planmod.PlanError("Sign-off needs gates G1–G7 to pass: " + "; ".join(failing))
    p = planmod.load(conn, deal["id"])
    dr = dryrun.latest(conn, deal["id"])
    pk = dr["predicted_kpis"]
    text = SIGNOFF_TEXT.format(hash=p["plan_hash"][:12], version=p["version"], dry=dr["id"],
                               accepted=dr["totals"]["accepted"], rejected=dr["totals"]["rejected"],
                               crit=pk["critical"] + pk["high"] + pk["medium"])
    sid = db.new_id("sign")
    ts = db.now()
    conn.execute("UPDATE signoffs SET revoked_at=?, revoked_by=? WHERE deal_id=? AND revoked_at IS NULL",
                 (ts, "superseded", deal["id"]))
    conn.execute("INSERT INTO signoffs (id, deal_id, plan_hash, dry_run_id, reviewer, statement, created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (sid, deal["id"], p["plan_hash"], dr["id"], reviewer, text, ts))
    planmod._event(conn, p, reviewer, "signoff", note=text)
    return latest_signoff(conn, deal["id"])


def revoke(conn, deal: dict, reviewer: str, note: str | None) -> dict:
    so = latest_signoff(conn, deal["id"])
    if not so or so["revoked_at"]:
        raise planmod.PlanError("There is no active sign-off to revoke.")
    conn.execute("UPDATE signoffs SET revoked_at=?, revoked_by=? WHERE id=?", (db.now(), reviewer, so["id"]))
    p = planmod.load(conn, deal["id"])
    planmod._event(conn, p, reviewer, "signoff_revoked", note=note)
    return latest_signoff(conn, deal["id"])


def compare(conn, deal: dict) -> dict:
    """Prediction (latest dry run) vs observation (latest post-import audit), by rule anchor."""
    dr = dryrun.latest(conn, deal["id"])
    run = latest_run(conn, deal["id"])
    if not dr or not run:
        return {"available": False, "has_dry_run": bool(dr), "has_audit": bool(run)}
    fs = metrics.load_findings(conn, deal["id"])
    observed = {f["payload"]["anchor"]: f for f in fs}
    predicted = {f["anchor"]: f for f in dr["predicted"]}
    _, _, counted = metrics.attribute_rent(fs)

    def obs_row(f):
        return {"finding_id": f["id"], "rule_id": f["rule_id"], "severity": f["severity"], "title": f["title"],
                "subject": f["subject"], "status": f["status"], "open": metrics.is_open(f),
                "rent_risk_cents": f["rent_risk_cents"], "deposit_cents": f["deposit_cents"],
                "rent_counted_cents": counted.get(f["id"], {}).get("counted", 0)}

    both = [obs_row(observed[a]) for a in sorted(set(observed) & set(predicted))]
    observed_only = sorted((obs_row(observed[a]) for a in set(observed) - set(predicted)),
                           key=lambda r: (metrics.SEVERITY_ORDER.get(r["severity"], 9),
                                          -(r["rent_risk_cents"] + r["deposit_cents"])))
    predicted_only = [{k: predicted[a][k] for k in ("rule_id", "severity", "title", "subject", "rent_risk_cents",
                                                   "deposit_cents")} for a in sorted(set(predicted) - set(observed))]
    summary = json.loads(run["summary_json"])
    actual = {c["id"]: c for c in summary["checks"]}
    sim = {c["id"]: c for c in dr["tieouts"]}
    rows = []
    for cid, c in sim.items():
        a = actual.get(cid)
        rows.append({"id": cid, "label": c["label"], "kind": c["kind"], "legacy": c["before"],
                     "simulated": c["after"], "actual": a["after"] if a else None,
                     "legacy_at_verify": a["before"] if a else None,
                     "simulated_ties": c["before"] == c["after"],
                     "actual_ties": bool(a) and a["before"] == a["after"]})
    legacy_moved = any(r["legacy_at_verify"] is not None and r["legacy_at_verify"] != r["legacy"] for r in rows)
    return {
        "available": True, "dry_run_id": dr["id"], "dry_run_at": dr["created_at"], "run_id": run["id"],
        "run_at": run["started_at"],
        "counts": {"predicted": len(predicted), "observed": len(observed), "both": len(both),
                   "observed_only": len(observed_only), "predicted_only": len(predicted_only)},
        "observed_only": observed_only, "both": both, "predicted_only": predicted_only,
        "tieouts": rows, "legacy_changed": legacy_moved,
        "note": ("Observed-only findings are import-time defects: the plan simulated cleanly, so they were introduced "
                 "by the actual load (or by edits after it). Only post-import verification catches these."),
    }


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def build_receipt(conn, deal: dict, reviewer: str, deal_status: dict | None) -> dict:
    from . import export  # local: export imports readiness
    r = evaluate(conn, deal, deal_status)
    if not r["release_ready"]:
        failing = [f"{x['id']} {x['label']}" for x in r["gates"] if x["status"] != "pass"]
        raise planmod.PlanError("A release receipt needs every gate to pass: " + "; ".join(failing))
    p = planmod.load(conn, deal["id"])
    dr = dryrun.latest(conn, deal["id"])
    so = latest_signoff(conn, deal["id"])
    run = latest_run(conn, deal["id"])
    cmp = compare(conn, deal)
    fs = metrics.load_findings(conn, deal["id"])
    by_status: dict[str, int] = {}
    for f in fs:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
    s = metrics.summarize(conn, deal, run["fingerprint"])
    manifest = export.manifest(conn, deal, p, dr, so)
    body = {
        "receipt": "MergeProof release receipt", "format": 1,
        "deal": {"id": deal["id"], "title": deal["title"], "synthetic": bool(deal["is_sample"]),
                 "legacy_system": deal["old_system"], "target_system": deal["new_system"]},
        "issued_at": db.now(), "issued_by": reviewer, "engine_version": config.ENGINE_VERSION,
        "template": {k: p["template"][k] for k in ("id", "name", "version")},
        "plan": {"hash": p["plan_hash"], "version": p["version"], "source_contract": p["contract_fp"]},
        "dry_run": {"id": dr["id"], "at": dr["created_at"], "simulated_output_sha256": dr["output_hash"],
                    "rows": dr["totals"], "predicted": dr["predicted_kpis"]},
        "signoff": {"reviewer": so["reviewer"], "at": so["created_at"], "statement": so["statement"],
                    "kind": "single reviewer"},
        "export": {"manifest_sha256": hashlib.sha256(_canon(manifest)).hexdigest(),
                   "files": {f["path"]: f["sha256"] for f in manifest["files"]}},
        "verification": {"run_id": run["id"], "at": run["started_at"], "fingerprint": run["fingerprint"],
                         "findings": {"total": len(fs), "by_status": by_status},
                         "continuity_score": s["continuity"]["score"]},
        "tieouts": cmp["tieouts"], "prediction_vs_observation": cmp["counts"],
        "gates": [{k: x[k] for k in ("id", "label", "status")} for x in r["gates"]],
        "limits": LIMITS + (["All data in this deal is synthetic demonstration data."] if deal["is_sample"] else []),
    }
    digest = hashlib.sha256(_canon(body)).hexdigest()
    rid = db.new_id("rcpt")
    conn.execute("INSERT INTO release_receipts (id, deal_id, sha256, receipt_json, created_at) VALUES (?,?,?,?,?)",
                 (rid, deal["id"], digest, _canon(body).decode(), body["issued_at"]))
    planmod._event(conn, p, reviewer, "release_receipt", note=f"Receipt {rid} sha256 {digest[:16]}…")
    return {"id": rid, "sha256": digest, "receipt": body}


def latest_receipt(conn, deal_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM release_receipts WHERE deal_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                     (deal_id,)).fetchone()
    if r is None:
        return None
    raw = r["receipt_json"]
    return {"id": r["id"], "sha256": r["sha256"], "receipt": json.loads(raw),
            "verifies": hashlib.sha256(raw.encode()).hexdigest() == r["sha256"]}


def _receipt_current(conn, deal_id: str, p: dict) -> bool:
    rc = latest_receipt(conn, deal_id)
    run = latest_run(conn, deal_id)
    return bool(rc and run and rc["receipt"]["plan"]["hash"] == p["plan_hash"]
                and rc["receipt"]["verification"]["run_id"] == run["id"])
