"""Migration Continuity Report: JSON for the in-app proof page, a PDF for the
integration meeting, and a CSV of findings with the current review state."""
from __future__ import annotations

import csv
import io
import re
from xml.sax.saxutils import escape

from . import config, db
from .engine import metrics
from .normalize import cents_to_str

ACTION_LABEL = {"investigate": "Investigating", "confirm_match": "Confirmed match", "expected_exception":
                "Expected exception", "resolve": "Resolved", "reopen": "Reopened", "comment": "Comment",
                "evidence_changed": "Evidence changed"}


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:60] or "audit"


def collect(conn, deal: dict, status: dict) -> dict:
    summary = metrics.summarize(conn, deal, status.get("fingerprint"))
    fs = metrics.load_findings(conn, deal["id"])
    _, _, counted = metrics.attribute_rent(fs)
    events = [dict(r) for r in conn.execute(
        "SELECT e.*, f.title, f.subject, f.rule_id FROM review_events e JOIN findings f ON f.id=e.finding_id "
        "WHERE e.deal_id=? ORDER BY e.created_at, e.id", (deal["id"],))]
    last_by_f: dict[str, dict] = {}
    for e in events:
        if e["action"] != "evidence_changed":
            last_by_f[e["finding_id"]] = e
    integration = _integration(conn, deal, status)
    predicted = integration["predicted_anchors"] if integration else None
    rows = []
    for f in sorted(fs, key=metrics._sev_key):
        r = metrics.finding_row(f, counted, deal)
        r["payload"] = f["payload"]
        r["last_review"] = last_by_f.get(f["id"])
        r["predicted_in_dry_run"] = None if predicted is None else f["payload"]["anchor"] in predicted
        rows.append(r)
    exceptions = [r for r in rows if r["status"] == "expected"]
    if integration:
        integration.pop("predicted_anchors")
    return {
        "integration": integration,
        "deal": {k: deal[k] for k in ("id", "title", "industry", "acquirer", "target_business", "deal_date",
                                      "old_system", "new_system", "source_notes", "source_ref")},
        "is_sample": bool(deal["is_sample"]), "generated_at": db.now(), "engine": config.ENGINE_VERSION,
        "status": status, "summary": summary, "findings": rows, "events": events, "exceptions": exceptions,
    }


def _integration(conn, deal: dict, status: dict) -> dict | None:
    """Plan / dry run / gates / receipt, when the deal has an integration plan."""
    from .integrate import dryrun, readiness
    from .integrate import plan as planmod
    p = planmod.load(conn, deal["id"])
    if p is None:
        return None
    dr = dryrun.latest(conn, deal["id"])
    r = readiness.evaluate(conn, deal, status)
    rc = readiness.latest_receipt(conn, deal["id"])
    return {
        "template": {k: p["template"][k] for k in ("id", "name", "version")}, "plan_hash": p["plan_hash"],
        "plan_version": p["version"], "counts": planmod.counts(p), "readiness": r,
        "dry_run": {k: dr[k] for k in ("id", "created_at", "plan_hash", "plan_version", "output_hash", "totals",
                                       "predicted_kpis", "banner", "importer_model")} if dr else None,
        "comparison": readiness.compare(conn, deal),
        "receipt": {"id": rc["id"], "sha256": rc["sha256"], "issued_at": rc["receipt"]["issued_at"],
                    "verifies": rc["verifies"]} if rc else None,
        "predicted_anchors": {f["anchor"] for f in dr["predicted"]} if dr else None,
    }


# --------------------------------------------------------------------------- CSV

_FORMULA = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def safe_cell(v) -> str:
    """Neutralize spreadsheet formula injection while keeping real numbers numeric."""
    if v is None:
        return ""
    s = str(v)
    if s and s[0] in _FORMULA and not _NUMERIC.match(s):
        return "'" + s
    return s


def _amount(c: int | None) -> str:
    return f"{(c or 0) / 100:.2f}"


def _diff_summary(payload: dict) -> tuple[str, str]:
    exp, obs = [], []
    for r in payload.get("compare") or []:
        if r["state"] in ("differs", "missing"):
            b = (r.get("before") or {}).get("value") or (r.get("before") or {}).get("raw") or ""
            a = (r.get("after") or {}).get("value") or (r.get("after") or {}).get("raw") or ("(none)" if r["state"] == "missing" else "")
            exp.append(f"{r['label']}: {b}")
            obs.append(f"{r['label']}: {a}")
    return "; ".join(exp), "; ".join(obs)


def render_csv(data: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["finding_id", "rule_id", "rule_name", "title", "severity", "status", "resolution", "needs_rereview",
                "entity", "subject", "est_monthly_rent_at_risk_counted", "rent_exposure_before_dedup",
                "rent_counted_under", "deposit_discrepancy", "rent_under_review", "confidence",
                "source_file", "source_row", "source_id", "target_file", "target_row", "target_id",
                "expected", "observed", "explanation", "recommended_action",
                "last_reviewer", "last_action", "last_note", "last_reviewed_at", "ai_suggestions",
                "material", "material_reason", "predicted_in_dry_run"])
    for r in data["findings"]:
        p = r["payload"]
        exp, obs = _diff_summary(p)
        lr = r.get("last_review") or {}
        src, tgt = r.get("source_ref") or {}, r.get("target_ref") or {}
        w.writerow([safe_cell(x) for x in [
            r["id"], r["rule_id"], r["rule_name"], r["title"], r["severity"], r["status"], r["resolution"] or "",
            "yes" if r["needs_rereview"] else "no", r["entity"], r["subject"], _amount(r["rent_counted_cents"]),
            _amount(r["rent_risk_cents"]), r["rent_covered_by"] or "", _amount(r["deposit_cents"]),
            _amount(r["pending_rent_cents"]), r["confidence"], src.get("file"), src.get("row"), src.get("id"),
            tgt.get("file"), tgt.get("row"), tgt.get("id"), exp, obs, p["explanation"], p["recommended_action"],
            lr.get("reviewer"), ACTION_LABEL.get(lr.get("action"), lr.get("action") or ""), lr.get("note"),
            lr.get("created_at"), sum(1 for c in p.get("candidates") or [] if c.get("origin") == "ai"),
            "yes" if r.get("material") else "no", r.get("material_reason") or "",
            {None: "no dry run", True: "yes", False: "no"}[r.get("predicted_in_dry_run")]]])
    return ("﻿" + out.getvalue()).encode("utf-8")


# --------------------------------------------------------------------------- PDF

_PDF_SUBS = {"→": "->", "−": "-", "≈": "~", "∅": "none", "≠": "!=", "✓": "OK", "✕": "X", "›": ">", "…": "...",
             "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": " - ", "×": "x", "·": "|", "▸": ">"}


def _cap(s, n: int = 400) -> str:
    """Keep table cells shorter than a page; a row taller than a page can't be laid out."""
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _plain(s) -> str:
    """Text safe for the built-in (cp1252) PDF fonts."""
    s = "" if s is None else str(s)
    for k, v in _PDF_SUBS.items():
        s = s.replace(k, v)
    return s.encode("cp1252", "replace").decode("cp1252")


def _t(s) -> str:
    """Same as _plain, escaped for reportlab Paragraph markup."""
    return escape(_plain(s))


def render_pdf(data: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    INK = colors.HexColor("#0E1726")
    PAPER = colors.HexColor("#F6F1E7")
    LINE = colors.HexColor("#D8CFBE")
    MUTED = colors.HexColor("#6B6455")
    LIME = colors.HexColor("#1F8F5F")
    CORAL = colors.HexColor("#D4533B")
    AMBER = colors.HexColor("#B7791F")
    SEV = {"critical": CORAL, "high": colors.HexColor("#C2622D"), "medium": AMBER, "low": MUTED}

    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=10, textColor=MUTED)
    mono = ParagraphStyle("mono", parent=base, fontName="Courier", fontSize=8, leading=10.5)
    h1 = ParagraphStyle("h1", parent=base, fontName="Times-Bold", fontSize=24, leading=28, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=16, spaceBefore=14,
                        spaceAfter=6)
    h3 = ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=9.5, leading=13, spaceBefore=8,
                        spaceAfter=3)
    kicker = ParagraphStyle("kicker", parent=base, fontName="Courier-Bold", fontSize=8, textColor=MUTED, leading=11)
    big = ParagraphStyle("big", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=20)

    d, s = data["deal"], data["summary"]
    k, cont, labels = s["kpis"], s["continuity"], s["labels"]
    W = LETTER[0] - 1.3 * inch

    def P(txt, st=base):
        return Paragraph(txt, st)

    def grid(rows, widths, header=True, zebra=True, font=8):
        rows = [[_plain(c) if isinstance(c, str) else c for c in row] for row in rows]
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [("FONT", (0, 0), (-1, -1), "Helvetica", font), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                 ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), INK), ("TEXTCOLOR", (0, 0), (-1, 0), PAPER),
                      ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font)]
        if zebra:
            for i in range(1 if header else 0, len(rows)):
                if i % 2 == 0:
                    style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FBF8F2")))
        t.setStyle(TableStyle(style))
        return t

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.rect(0, LETTER[1] - 0.42 * inch, LETTER[0], 0.42 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#C6F36B"))
        canvas.setFont("Courier-Bold", 8.5)
        canvas.drawString(0.65 * inch, LETTER[1] - 0.26 * inch, "MERGEPROOF")
        canvas.setFillColor(PAPER)
        canvas.setFont("Courier", 8)
        canvas.drawString(1.55 * inch, LETTER[1] - 0.26 * inch, "/ MIGRATION CONTINUITY REPORT")
        canvas.drawRightString(LETTER[0] - 0.65 * inch, LETTER[1] - 0.26 * inch, _plain(d["title"])[:70])
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        foot = (f"Generated {data['generated_at'][:19].replace('T', ' ')} UTC  |  {data['engine']}  |  "
                f"Run {s['run']['id']}")
        if data["is_sample"]:
            foot += "  |  SYNTHETIC DEMONSTRATION DATA"
        canvas.drawString(0.65 * inch, 0.45 * inch, foot)
        canvas.drawRightString(LETTER[0] - 0.65 * inch, 0.45 * inch, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.65 * inch, rightMargin=0.65 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch, title=f"Continuity Report - {d['title']}",
                            author="MergeProof", subject="Migration Continuity Report")
    story = []
    story.append(P("MIGRATION CONTINUITY REPORT", kicker))
    story.append(P(_t(d["title"]), h1))
    meta = [
        ["Acquirer", _t(d.get("acquirer") or "-"), "Business integrated", _t(d.get("target_business") or "-")],
        ["Legacy system", _t(d.get("old_system") or "-"), "Consolidated system", _t(d.get("new_system") or "-")],
        ["Cutover / as-of", _t(d.get("deal_date") or "-"), "Audit run", _t(s["run"]["finished_at"][:19].replace("T", " ") + " UTC")],
        ["Industry", _t(d.get("industry") or "-"), "Report status", _t(data["status"]["state"].replace("_", " "))],
    ]
    mt = Table([[P(f"<font color='#6B6455'>{a}</font>", small), P(b), P(f"<font color='#6B6455'>{c}</font>", small),
                 P(e)] for a, b, c, e in meta], colWidths=[1.05 * inch, 2.2 * inch, 1.3 * inch, 2.65 * inch])
    mt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [mt, Spacer(1, 10)]
    if data["status"]["state"] == "stale":
        story.append(P("<font color='#D4533B'><b>Note:</b> source data or match decisions changed after this run. "
                       "Rerun the audit for current results.</font>"))

    def kpi(label, value, sub, color=INK):
        return [P(f"<font name='Courier' size='7' color='#6B6455'>{_t(label.upper())}</font>"),
                P(f"<font color='{color.hexval().replace('0x', '#')}'>{_t(value)}</font>", big), P(_t(sub), small)]

    score = cont["score"]
    kp = Table([[kpi("Continuity score", f"{score:.1f}%" if score is not None else "-",
                     f"{cont['accounted']} of {cont['active_leases']} active leases accounted for", LIME),
                 kpi("Est. monthly rent at risk", cents_to_str(k["rent_at_risk_open"]),
                     "requires review; open findings only, deduplicated per lease", CORAL),
                 kpi("Deposit discrepancies", cents_to_str(k["deposit_open"]),
                     f"{k['deposit_open_count']} open; a liability, not added to rent", AMBER),
                 kpi("Open findings", str(k["findings_open"]),
                     f"{k['critical_open']} critical, {k['high_open']} high; {k['findings_closed']} closed")]],
               colWidths=[W / 4] * 4)
    kp.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, INK), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBF8F2")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 7),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [kp, Spacer(1, 6)]
    story.append(P(f"Continuity score = active legacy leases with a confident counterpart in {labels['new']} (or "
                   f"closed by a reviewer), divided by all {cont['active_leases']} active legacy leases. It measures "
                   f"reconciliation completeness, not legal compliance or overall migration quality. "
                   f"{cont['clean']} leases ({cont['clean_pct']}%) are matched with no open discrepancies.", small))

    story.append(P("Estimated monthly rent at risk — methodology", h2))
    story.append(P("Each legacy lease contributes at most one amount: the largest open exposure among its findings "
                   "(a missing or inactivated lease contributes its full monthly rent; a lower rent on a matched lease "
                   "contributes the difference). Rent increases, leases under review and deposit differences are "
                   "excluded. This is an estimate that requires review, not realized revenue loss."))
    rl = [["Finding", "Lease / subject", "Counted", "Also detected (not added)"]]
    for line in s["rent_lines"]:
        also = "; ".join(f"{a['rule_id']} {a['title']} ({cents_to_str(a['cents'])})" for a in line["also"]) or "-"
        rl.append([P(f"{line['rule_id']} {_t(line['title'])}"), P(_t(line["subject"])),
                   P(f"<font name='Courier'>{cents_to_str(line['cents'])}</font>"), P(_t(also), small)])
    rl.append([P("<b>Total</b>"), "", P(f"<font name='Courier'><b>{cents_to_str(k['rent_at_risk_open'])}</b></font>"),
               P(f"Pending review (not counted): {cents_to_str(k['pending_review_rent'])}", small)])
    story.append(grid(rl, [1.9 * inch, 2.3 * inch, 0.9 * inch, W - 5.1 * inch]))

    story.append(P("Deposit balance discrepancies (tracked separately)", h2))
    dep = [["Finding", "Subject", "Amount", "Status"]] + [
        [P(f"{r['rule_id']} {_t(r['title'])}"), P(_t(r["subject"])),
         P(f"<font name='Courier'>{cents_to_str(r['deposit_cents'])}</font>"), r["status"]]
        for r in data["findings"] if r["deposit_cents"]]
    if len(dep) == 1:
        dep.append(["-", "No deposit discrepancies", "-", "-"])
    story.append(grid(dep, [2.2 * inch, 3.0 * inch, 1.0 * inch, W - 6.2 * inch]))

    story.append(P("Snapshots compared", h2))
    files = [["Side", "File", "Record type", "Rows"]] + [
        ["Legacy" if f["side"] == "before" else "Consolidated", P(_t(f["file"])), f["entity"], f"{f['rows']:,}"]
        for f in s["files"]]
    story.append(grid(files, [1.1 * inch, 3.4 * inch, 1.5 * inch, W - 6.0 * inch]))
    for sk in s.get("skipped") or []:
        story.append(P(_t(sk), small))

    story.append(P("Portfolio totals (aggregate invariants)", h2))
    story.append(P("Totals are shown for context. Equal totals can hide offsetting errors, so every total is paired "
                   "with row-level evidence below.", small))

    def fmt(v, kind):
        return cents_to_str(v) if kind == "money" else f"{v:,}"

    ag = [["Check", "Legacy", "Consolidated", "Delta", "Result", "Note"]]
    for ch in s["checks"]:
        delta = ch["delta"]
        dtxt = (cents_to_str(delta, signed=True) if ch["kind"] == "money" else f"{delta:+,}") if delta is not None else "-"
        col = {"pass": "#1F8F5F", "warn": "#B7791F", "fail": "#D4533B"}[ch["status"]]
        ag.append([P(_t(ch["label"])), fmt(ch["before"], ch["kind"]), fmt(ch["after"], ch["kind"]), dtxt,
                   P(f"<font color='{col}'><b>{ch['status'].upper()}</b></font>"), P(_t(ch["note"]), small)])
    story.append(grid(ag, [1.45 * inch, 0.85 * inch, 0.9 * inch, 0.75 * inch, 0.5 * inch, W - 4.45 * inch]))

    story.append(P("Record coverage", h2))
    cov = [["Record type", "Legacy", "Consolidated", "Confirmed", "Probable", "Reviewer", "Needs review", "Unmatched"]]
    for ent in ("properties", "units", "tenants", "leases", "maintenance", "deposits"):
        c = s["counts"].get(ent)
        if not c:
            continue
        cov.append([ent, f"{c['before']:,}", f"{c['after']:,}", str(c.get("confirmed", "-")), str(c.get("probable", "-")),
                    str(c.get("reviewer_confirmed", "-")), str(c.get("needs_review", "-")), str(c.get("unmatched", "-"))])
    story.append(grid(cov, [1.2 * inch] + [(W - 1.2 * inch) / 7] * 7))
    story.append(P("Deposits are reconciled through their matched lease rather than paired directly.", small))

    if s.get("benign"):
        story.append(P("Recognized as harmless (no finding raised)", h3))
        bn = [["Change", "Count", "Example (legacy -> consolidated)"]] + [
            [k_, f"{v['count']:,}", P(_t("; ".join(f"{a} -> {b}" for a, b in v["examples"][:2])), small)]
            for k_, v in s["benign"].items()]
        story.append(grid(bn, [1.8 * inch, 0.7 * inch, W - 2.5 * inch]))

    story.append(P("Checks performed", h2))
    ck = [["Rule", "What is verified", "Checked", "Findings"]] + [
        [r["rule_id"], P(_t(r["check"]), small), f"{r['records_checked']:,}", str(r["findings"])] for r in s["rules"]]
    story.append(grid(ck, [0.55 * inch, W - 1.9 * inch, 0.7 * inch, 0.65 * inch], font=7.5))

    ig = data.get("integration")
    if ig:
        story.append(PageBreak())
        story.append(P("Integration plan, dry run and release gates", h2))
        dr = ig["dry_run"]
        so = ig["readiness"].get("signoff")
        story.append(P(_t(f"Target template {ig['template']['name']} ({ig['template']['version']}). Plan "
                          f"{ig['plan_hash'][:16]} version {ig['plan_version']}: {ig['counts']['decided']} of "
                          f"{ig['counts']['total']} columns decided by a named reviewer. "
                          + (f"Signed off by {so['reviewer']} (single reviewer) at {so['created_at'][:16].replace('T', ' ')} UTC."
                             if so and not so.get("revoked_at") else "Not signed off.")), small))
        if dr:
            pk = dr["predicted_kpis"]
            story.append(P(_t(f"{dr['banner']}. Dry run {dr['id']} (plan v{dr['plan_version']}): "
                              f"{dr['totals']['accepted']:,} of {dr['totals']['source_rows']:,} rows simulated, "
                              f"{dr['totals']['rejected']:,} rejected; {pk['findings']} predicted finding(s), "
                              f"{pk['critical']} critical / {pk['high']} high. {dr['importer_model']}"), small))
        cmp_ = ig["comparison"]
        if cmp_.get("available"):
            def f3(v, kind):
                return "-" if v is None else (cents_to_str(v) if kind == "money" else f"{v:,}")
            t3 = [["Check", "Legacy", "Simulated", "Actual (after load)"]]
            for t in cmp_["tieouts"]:
                t3.append([P(_t(t["label"])), P(f3(t["legacy"], t["kind"])), P(f3(t["simulated"], t["kind"])),
                           P(("" if t["actual_ties"] else "<font color='#D4533B'><b>") + f3(t["actual"], t["kind"]) +
                             ("" if t["actual_ties"] else "</b></font>"))])
            story.append(grid(t3, [2.2 * inch, 1.3 * inch, 1.3 * inch, W - 4.8 * inch]))
            c = cmp_["counts"]
            story.append(P(_t(f"Prediction vs observation: {c['predicted']} predicted by the dry run, {c['observed']} "
                              f"observed after the load; {c['both']} both, {c['observed_only']} observed only "
                              f"(import-time defects), {c['predicted_only']} predicted only. {cmp_['note']}"), small))
        gt = [["Gate", "Status", "Detail"]]
        for g in ig["readiness"]["gates"]:
            col = {"pass": "#1F8F5F", "fail": "#D4533B", "pending": "#6B6455"}[g["status"]]
            gt.append([P(_t(f"{g['id']} {g['label']}"), small), P(f"<font color='{col}'><b>{g['status'].upper()}</b></font>"),
                       P(_t(g["detail"]), small)])
        story.append(grid(gt, [3.0 * inch, 0.7 * inch, W - 3.7 * inch], font=7.5))
        if ig["receipt"]:
            rc = ig["receipt"]
            story.append(P(_t(f"Release receipt {rc['id']} issued {rc['issued_at'][:16].replace('T', ' ')} UTC, sha256 "
                              f"{rc['sha256']}" + ("" if rc["verifies"] else " — STORED RECEIPT NO LONGER MATCHES ITS HASH")),
                           mono))

    story.append(PageBreak())
    story.append(P("Findings register", h2))
    sev = s["severity_open"]
    story.append(P(f"Open by severity: critical {sev['critical']} | high {sev['high']} | medium {sev['medium']} | "
                   f"low {sev['low']}. Statuses reflect reviewer decisions at export time.", small))
    fr = [["ID", "Finding", "Subject", "Monthly effect", "Status", "Evidence"]]
    for r in data["findings"]:
        if r["rent_counted_cents"]:
            eff = f"-{cents_to_str(r['rent_counted_cents'])}/mo"
        elif r["rent_covered_by"]:
            eff = f"counted under {r['rent_covered_by']}"
        elif r["deposit_cents"]:
            eff = f"deposit {cents_to_str(r['deposit_cents'])}"
        elif r["pending_rent_cents"]:
            eff = f"under review {cents_to_str(r['pending_rent_cents'])}/mo"
        else:
            eff = "-"
        src = r.get("source_ref") or r.get("target_ref") or {}
        evid = f"{src.get('file')}:{src.get('row')}" if src.get("file") else "-"
        col = SEV.get(r["severity"], MUTED).hexval().replace("0x", "#")
        st = r["status"] + (" (re-review)" if r["needs_rereview"] else "")
        fr.append([P(f"<font name='Courier' size='7'>{r['id']}</font>"),
                   P(f"<font color='{col}'><b>{r['severity'].upper()}</b></font> {r['rule_id']} {_t(r['title'])}"),
                   P(_t(_cap(r["subject"], 200)), small), P(_t(eff), small), P(_t(st), small), P(_t(evid), small)])
    story.append(grid(fr, [0.95 * inch, 1.85 * inch, 1.6 * inch, 1.05 * inch, 0.75 * inch, W - 6.2 * inch]))

    outstanding = [r for r in data["findings"] if r["status"] in ("open", "investigating") and
                   r["confidence"] == "needs_review"]
    story.append(P("Outstanding decisions", h2))
    if outstanding:
        for r in outstanding:
            story.append(P(f"<b>{r['id']}</b> {_t(r['title'])} — {_t(r['subject'])}. {_t(r['payload']['recommended_action'])}"))
    else:
        story.append(P("No ambiguous matches are awaiting a decision."))

    story.append(P("Expected exceptions and reviewer notes", h2))
    if data["exceptions"]:
        ex = [["Finding", "Reason", "Reviewer", "When"]]
        for r in data["exceptions"]:
            lr = r.get("last_review") or {}
            ex.append([P(f"{r['id']} {_t(r['title'])}<br/><font size='7' color='#6B6455'>{_t(r['subject'])}</font>"),
                       P(_t(_cap(lr.get("note") or "-", 900))), P(_t(lr.get("reviewer") or "-")),
                       P(_t((lr.get("created_at") or "")[:16].replace("T", " ")), small)])
        story.append(grid(ex, [2.2 * inch, W - 4.3 * inch, 1.1 * inch, 1.0 * inch]))
    else:
        story.append(P("No findings have been marked as expected exceptions."))

    story.append(P("Review log", h2))
    if data["events"]:
        lg = [["When (UTC)", "Reviewer", "Action", "Finding", "Note"]]
        for e in data["events"]:
            lg.append([P(_t(e["created_at"][:19].replace("T", " ")), small), P(_t(e["reviewer"])),
                       P(_t(ACTION_LABEL.get(e["action"], e["action"]) + f" ({e['from_status']} -> {e['to_status']})"),
                         small), P(f"{e['finding_id']} {_t(e['title'])}", small), P(_t(_cap(e["note"] or "", 900)), small)])
        story.append(grid(lg, [1.05 * inch, 0.95 * inch, 1.35 * inch, 1.9 * inch, W - 5.25 * inch]))
    else:
        story.append(P("No review actions yet."))

    story.append(PageBreak())
    story.append(P("Evidence appendix", h2))
    story.append(P("Each finding lists the exact source rows (file and spreadsheet row number, header = row 1) and the "
                   "fields compared. 'format only' means values differ in formatting but are equal after "
                   "normalization; 're-keyed' marks IDs that changed between systems, which is expected.", small))
    state_lbl = {"same": "same", "format": "format only", "rekeyed": "re-keyed", "differs": "DIFFERS",
                 "missing": "MISSING", "na": "not in export"}
    for r in data["findings"]:
        p = r["payload"]
        block = [P(f"<font name='Courier' size='8' color='#6B6455'>{r['id']} / {r['rule_id']} / "
                   f"{r['severity'].upper()} / {r['status']}</font>"),
                 P(f"<b>{_t(r['title'])}</b> — {_t(r['subject'])}", h3), P(_t(p["explanation"]))]
        refs = []
        if p.get("source"):
            refs.append(f"Legacy: {p['source']['file']} row {p['source']['row']} (ID {p['source'].get('id') or '-'})")
        if p.get("target"):
            refs.append(f"Consolidated: {p['target']['file']} row {p['target']['row']} (ID {p['target'].get('id') or '-'})")
        for x in p.get("extra_targets") or []:
            refs.append(f"Consolidated: {x['file']} row {x['row']} (ID {x.get('id') or '-'})")
        if refs:
            block.append(P(_t("  |  ".join(refs)), mono))
        if p.get("compare"):
            ct = [["Field", "Legacy / expected", "Consolidated / actual", ""]]
            for c in p["compare"]:
                b = c.get("before") or {}
                a = c.get("after") or {}
                bv = b.get("raw") or b.get("value") or "-"
                av = a.get("raw") or a.get("value") or ("no matching record" if c["state"] == "missing" else "-")
                ct.append([c["label"], P(_t(_cap(bv)), small), P(_t(_cap(av)), small),
                           state_lbl.get(c["state"], c["state"])])
            t = grid(ct, [1.2 * inch, 2.45 * inch, 2.45 * inch, W - 6.1 * inch], font=7.5)
            block.append(t)
        if p.get("impact", {}).get("calc"):
            block.append(P(_t("  |  ".join(f"{a}: {b}" for a, b in p["impact"]["calc"])), mono))
        if p.get("candidates"):
            for cnd in p["candidates"]:
                rec = cnd.get("record") or {}
                block.append(P(_t(f"Candidate {rec.get('id')} ({rec.get('file')} row {rec.get('row')}) — score "
                                  f"{cnd['score']}/100: " + "; ".join(f"{x[0]} (+{x[1]})" for x in cnd["reasons"])),
                               small))
        if p.get("members"):
            block.append(P(_t("Rows: " + ", ".join(f"{m.get('file')}:{m.get('row')}" for m in p["members"][:40])),
                           small))
        block.append(P(f"<b>Recommended:</b> {_t(p['recommended_action'])}", small))
        block.append(Spacer(1, 8))
        story.append(KeepTogether(block[:4]))
        story += block[4:]

    story.append(P("AI assistance", h2))
    ai = s.get("ai") or {}
    if ai.get("available"):
        story.append(P(_t(f"A local open-source model ({ai.get('model')}) ran on the MergeProof server and made "
                          f"{ai.get('suggestions', 0)} suggestion(s) for a reviewer ({'; '.join(ai.get('used_for') or [])}) "
                          f"and {ai.get('decisions_by_ai', 0)} decisions. Every finding, match, amount and score in this "
                          f"report comes from the deterministic rules or a named reviewer's decision. No data was sent "
                          f"to an external service.")))
    else:
        story.append(P(_t(f"AI assistance was not used for this run ({ai.get('reason') or 'not available'}). Every "
                          f"result comes from the deterministic rules.")))

    story.append(P("Limitations", h2))
    story.append(P("MergeProof compares customer-provided exports; it does not connect to or modify any property-"
                   "management system. Matching is deterministic and rule-based; ambiguous pairings are left for a "
                   "reviewer. Amounts are estimates for review and are not accounting or legal conclusions."
                   + (" All data in this report is synthetic demonstration data." if data["is_sample"] else ""), small))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
