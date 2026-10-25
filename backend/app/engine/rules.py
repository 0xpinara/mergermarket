"""Business-invariant checks. Each check emits findings with side-by-side
evidence, a plain-English explanation and, where justified, a money impact.

Money semantics:
  * rent_risk_cents  — recurring monthly rent that may not bill. Deduplicated per
                       legacy lease (risk_key): a lease contributes at most the
                       largest open exposure among its findings, never the sum.
  * deposit_cents    — security-deposit liability discrepancy. Never added to rent.
  * pending_rent     — rent on a lease whose match is under human review; shown
                       separately, not counted as at-risk.
"""
from __future__ import annotations

from collections import defaultdict

from .. import normalize as nz
from ..ai import assist
from .canonical import Rec, display_name
from .matching import TH, Result, Side, name_similarity, same_tenant_ref, same_unit

RULES: dict[str, dict] = {
    "L-001": {"name": "Missing lease", "entity": "leases", "severity": "critical",
              "check": "Every lease that is active in the legacy export has a confident counterpart after migration."},
    "L-002": {"name": "Rent changed (lower)", "entity": "leases", "severity": "high",
              "check": "Monthly rent on each matched active lease is unchanged or higher."},
    "L-003": {"name": "Rent changed (higher)", "entity": "leases", "severity": "medium",
              "check": "Rent increases are surfaced for review (possible overbilling), not counted as risk."},
    "L-004": {"name": "Lease no longer active", "entity": "leases", "severity": "high",
              "check": "Leases active before migration are still active after it."},
    "L-005": {"name": "Lease dates changed", "entity": "leases", "severity": "medium",
              "check": "Start and end dates survive the move."},
    "L-006": {"name": "Needs review: possible lease match", "entity": "leases", "severity": "high",
              "check": "Ambiguous lease pairings are routed to a human instead of being auto-accepted."},
    "L-007": {"name": "Lease linked to wrong unit", "entity": "leases", "severity": "high",
              "check": "Each matched lease is attached to the same unit it occupied before."},
    "L-008": {"name": "Lease linked to wrong tenant", "entity": "leases", "severity": "high",
              "check": "Each matched lease is attached to the same tenant as before."},
    "L-009": {"name": "Lease points to a missing unit", "entity": "leases", "severity": "high",
              "check": "Every consolidated lease references a unit that exists."},
    "L-010": {"name": "Lease points to a missing tenant", "entity": "leases", "severity": "high",
              "check": "Every consolidated lease references a tenant that exists."},
    "L-011": {"name": "Active lease with no legacy record", "entity": "leases", "severity": "low",
              "check": "Active consolidated leases trace back to a legacy lease (or are confirmed as new)."},
    "L-012": {"name": "Ended lease not carried over", "entity": "leases", "severity": "low",
              "check": "Lease history (expired/cancelled) is retained. Informational — no current rent."},
    "L-013": {"name": "Duplicate lease", "entity": "leases", "severity": "high",
              "check": "No legacy lease is represented by two consolidated leases (double-billing risk)."},
    "L-014": {"name": "Ended lease shows as active", "entity": "leases", "severity": "medium",
              "check": "Leases that had ended are not reactivated by the migration."},
    "L-015": {"name": "Upcoming lease missing", "entity": "leases", "severity": "high",
              "check": "Signed future leases carry over."},
    "L-016": {"name": "Lease with unrecognized status missing", "entity": "leases", "severity": "high",
              "check": "Leases whose status can't be interpreted are routed to review rather than assumed ended."},
    "T-001": {"name": "Duplicate tenant", "entity": "tenants", "severity": "high",
              "check": "Each person appears once (no duplicate tenant records with the same email/phone)."},
    "D-001": {"name": "Deposit balance missing", "entity": "deposits", "severity": "high",
              "check": "Every security deposit held before migration is recorded after it."},
    "D-002": {"name": "Deposit balance differs", "entity": "deposits", "severity": "high",
              "check": "Held deposit balances match to the cent."},
    "D-004": {"name": "Deposit not linked to a lease", "entity": "deposits", "severity": "medium",
              "check": "Every deposit row references a lease or tenant that can be found."},
    "D-005": {"name": "Deposit balance blank (not zero)", "entity": "deposits", "severity": "high",
              "check": "A blank deposit balance is unknown, never zero: it is flagged on either side, not netted."},
    "D-003": {"name": "Deposit with no legacy record", "entity": "deposits", "severity": "low",
              "check": "Deposits in the consolidated system trace back to the legacy ledger."},
    "M-001": {"name": "Open work order missing", "entity": "maintenance", "severity": "high",
              "check": "Open and in-progress maintenance requests carry over."},
    "M-002": {"name": "Open work order closed in migration", "entity": "maintenance", "severity": "high",
              "check": "Requests that were open are not marked closed by the migration."},
    "M-003": {"name": "Work order priority lowered", "entity": "maintenance", "severity": "medium",
              "check": "Urgent/high priorities survive the move."},
    "M-004": {"name": "Completed work orders not carried over", "entity": "maintenance", "severity": "low",
              "check": "Completed request history is retained. Informational."},
    "M-005": {"name": "Needs review: possible work-order match", "entity": "maintenance", "severity": "medium",
              "check": "Ambiguous work-order pairings are routed to a human."},
    "P-001": {"name": "Property missing", "entity": "properties", "severity": "critical",
              "check": "Every legacy property exists after migration."},
    "P-002": {"name": "Needs review: possible property match", "entity": "properties", "severity": "high",
              "check": "Ambiguous property pairings are routed to a human."},
    "U-001": {"name": "Unit missing", "entity": "units", "severity": "medium",
              "check": "Every legacy unit exists in its matched property."},
    "U-002": {"name": "Unit points to a missing property", "entity": "units", "severity": "medium",
              "check": "Every consolidated unit references a property that exists."},
    "T-003": {"name": "Needs review: possible tenant match", "entity": "tenants", "severity": "medium",
              "check": "Ambiguous tenant identities are routed to a human."},
    "Q-001": {"name": "Duplicate ID in an export", "entity": "data_quality", "severity": "medium",
              "check": "Record IDs are unique within each export."},
    "Q-002": {"name": "Unreadable values", "entity": "data_quality", "severity": "medium",
              "check": "Money and date fields parse cleanly."},
    "Q-003": {"name": "Broken references in an export", "entity": "data_quality", "severity": "low",
              "check": "References inside each export (lease → unit, unit → property) resolve."},
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _money(c):
    return nz.cents_to_str(c)


def _disp(kind: str, raw, norm):
    if raw is None and norm is None:
        return None
    if kind == "money":
        v = _money(norm) if norm is not None else None
    elif kind in ("date", "id"):
        v = norm
    elif kind in ("lease_status", "ticket_status", "priority"):
        v = (norm or "").replace("_", " ") or None
    else:
        v = raw
    return {"raw": raw if raw not in (None, "") else None, "value": v}


def mapped_both(s: Rec | None, t: Rec | None, field: str) -> bool:
    return bool(s and t and field in s.f and field in t.f)


def crow(field: str, label: str, kind: str, s_raw=None, t_raw=None, s_norm=None, t_norm=None,
         state: str | None = None, missing_after: bool = False) -> dict:
    if missing_after:
        return {"field": field, "label": label, "kind": kind, "before": _disp(kind, s_raw, s_norm), "after": None,
                "state": "missing"}
    if state is None:
        sb, tb = s_raw in (None, ""), t_raw in (None, "")
        if sb and tb:
            state = "same"
        elif s_norm == t_norm:
            state = "same" if (s_raw or "").strip() == (t_raw or "").strip() else "format"
        else:
            state = "differs"
    return {"field": field, "label": label, "kind": kind, "before": _disp(kind, s_raw, s_norm),
            "after": _disp(kind, t_raw, t_norm), "state": state}


def ev(rec: Rec | None, context: dict | None = None) -> dict | None:
    if rec is None:
        return None
    return {"key": rec.key, "id": rec.id, "file": rec.file, "row": rec.row, "side": rec.side, "entity": rec.entity,
            "raw": rec.raw, "issues": rec.issues, "context": context or {}}


def match_info(m, method: str = "") -> dict | None:
    if m is None:
        return None
    return {"status": m.status, "score": m.score, "reasons": m.reasons, "notes": m.notes, "method": method}


class Ctx:
    def __init__(self, S: Side, T: Side, M: dict[str, Result], labels: dict, as_of: str | None, ai=None):
        self.S, self.T, self.M, self.L, self.as_of = S, T, M, labels, as_of
        self.ai = ai  # local embedder or None; used only to *suggest* candidates
        self.findings: list[dict] = []

    def add(self, rule_id: str, anchor: str, *, subject: str, explanation: str, action: str,
            source=None, target=None, compare=None, match=None, candidates=None, impact=None,
            rent_risk: int = 0, risk_key: str | None = None, deposit: int = 0, pending_rent: int = 0,
            keys: dict | None = None, severity: str | None = None, title: str | None = None,
            members: list | None = None, extra_targets: list | None = None, confidence: str | None = None):
        rule = RULES[rule_id]
        self.findings.append({
            "anchor": f"{rule_id}|{anchor}", "rule_id": rule_id, "title": title or rule["name"],
            "severity": severity or rule["severity"], "entity": rule["entity"], "subject": subject,
            "explanation": explanation, "recommended_action": action,
            "source": source, "target": target, "extra_targets": extra_targets or [],
            "compare": compare or [], "match": match, "candidates": candidates or [],
            "impact": impact or {"type": "none", "cents": 0},
            "rent_risk_cents": int(rent_risk or 0), "risk_key": risk_key, "deposit_cents": int(deposit or 0),
            "pending_rent_cents": int(pending_rent or 0), "keys": keys or {}, "members": members or [],
            "confidence": confidence or (match or {}).get("status") or "unmatched",
        })


# --------------------------------------------------------------------------- helpers

def _lease_ctx(side: Side, lease_key: str) -> dict:
    l = side.leases[lease_key]
    uk = side.lease_unit.get(lease_key)
    tk = side.lease_tenant.get(lease_key)
    unit = side.unit_text(uk)
    if unit is None and not side.units and l.f.get("unit_id"):
        unit = f"Unit {l.f['unit_id']}" + (f" · {l.f['property_id']}" if l.f.get("property_id") else "")
    tenant = side.tenant_name(tk)
    if tenant is None and not side.tenants:
        tenant = l.f.get("tenant_id") or None
    return {"unit": unit, "tenant": tenant, "unit_key": uk, "tenant_key": tk,
            "property_key": side.unit_prop.get(uk) if uk else None, "lease": l}


def _clip(v: str | None, n: int = 120) -> str | None:
    return v if v is None or len(v) <= n else v[: n - 1] + "…"


def _subject(tenant: str | None, unit: str | None) -> str:
    return " · ".join(_clip(p) for p in (tenant, unit) if p) or "Unlabelled record"


def _lease_compare(c: Ctx, s: Rec, t: Rec | None) -> list[dict]:
    S, T = c.S, c.T
    sc = _lease_ctx(S, s.key)
    if t is None:
        return [crow("lease_id", "Lease ID", "id", s.f.get("lease_id"), None, s.id, None, missing_after=True),
                crow("tenant", "Tenant", "text", sc["tenant"], None, missing_after=True),
                crow("unit", "Unit", "text", sc["unit"], None, missing_after=True),
                crow("start_date", "Start date", "date", s.f.get("start_date"), None, s.n.get("start_date"),
                     missing_after=True),
                crow("end_date", "End date", "date", s.f.get("end_date"), None, s.n.get("end_date"), missing_after=True),
                crow("status", "Status", "lease_status", s.f.get("status"), None, s.n.get("state"), missing_after=True),
                crow("monthly_rent", "Monthly rent", "money", s.f.get("monthly_rent"), None, s.n.get("monthly_rent"),
                     missing_after=True)]
    tc = _lease_ctx(T, t.key)
    exp_unit = c.M["units"].matches.get(sc["unit_key"]).t if sc["unit_key"] in c.M["units"].matches else None
    exp_ten = c.M["tenants"].target_set(sc["tenant_key"]) if sc["tenant_key"] else set()
    unit_after = tc["unit"] or (f"No unit with ref '{t.f.get('unit_id')}'" if t.f.get("unit_id") else None)
    ten_after = tc["tenant"] or (f"No tenant with ref '{t.f.get('tenant_id')}'" if t.f.get("tenant_id") else None)
    if c.S.units and c.T.units:
        same_u = bool(tc["unit_key"] and tc["unit_key"] == exp_unit)
    else:
        same_u = bool(same_unit(c.S, c.T, c.M["units"], s.key, t.key))
    unit_state = "same" if same_u else "differs"
    if unit_state == "same" and (sc["unit"] or "") != (tc["unit"] or ""):
        unit_state = "format"
    if c.S.tenants and c.T.tenants:
        same_t = bool(tc["tenant_key"] and tc["tenant_key"] in exp_ten)
    else:
        same_t = bool(same_tenant_ref(c.S, c.T, s.key, t.key))
    ten_state = "same" if same_t else "differs"
    return [
        crow("lease_id", "Lease ID", "id", s.f.get("lease_id"), t.f.get("lease_id"), s.id, t.id,
             state="rekeyed" if s.id != t.id else "same"),
        crow("tenant", "Tenant", "text", sc["tenant"], ten_after, state=ten_state),
        crow("unit", "Unit", "text", sc["unit"], unit_after, state=unit_state),
        crow("start_date", "Start date", "date", s.f.get("start_date"), t.f.get("start_date"),
             s.n.get("start_date"), t.n.get("start_date"),
             state=None if mapped_both(s, t, "start_date") else "na"),
        crow("end_date", "End date", "date", s.f.get("end_date"), t.f.get("end_date"),
             s.n.get("end_date"), t.n.get("end_date"), state=None if mapped_both(s, t, "end_date") else "na"),
        crow("status", "Status", "lease_status", s.f.get("status"), t.f.get("status"), s.n.get("state"),
             t.n.get("state"), state=None if mapped_both(s, t, "status") else "na"),
        crow("monthly_rent", "Monthly rent", "money", s.f.get("monthly_rent"), t.f.get("monthly_rent"),
             s.n.get("monthly_rent"), t.n.get("monthly_rent")),
    ]


def _keys(c: Ctx, lease_key: str | None = None, unit_key: str | None = None, tenant_key: str | None = None,
          property_key: str | None = None) -> dict:
    S = c.S
    if lease_key:
        unit_key = unit_key or S.lease_unit.get(lease_key)
        tenant_key = tenant_key or S.lease_tenant.get(lease_key)
    if unit_key:
        property_key = property_key or S.unit_prop.get(unit_key)
    return {"lease": lease_key, "unit": unit_key, "tenant": tenant_key, "property": property_key}


def _candidate_view(c: Ctx, entity: str, cand: dict, describe) -> dict:
    p = cand["pair"]
    rec = {"leases": c.T.leases, "tenants": c.T.tenants, "maintenance": c.T.tickets, "properties": c.T.props,
           "units": c.T.units}[entity][p.t]
    return {"target_key": p.t, "score": p.score, "reasons": p.reasons, "notes": p.notes,
            "taken_by": cand["taken_by"], "record": ev(rec, describe(rec)), "summary": describe(rec)}


def _describe_target_lease(c: Ctx):
    def f(rec: Rec) -> dict:
        tc = _lease_ctx(c.T, rec.key)
        return {"tenant": tc["tenant"], "unit": tc["unit"] or f"unresolved ref {rec.f.get('unit_id')}",
                "start_date": rec.n.get("start_date"), "end_date": rec.n.get("end_date"),
                "status": rec.n.get("state"), "monthly_rent": _money(rec.n.get("monthly_rent"))}
    return f


# --------------------------------------------------------------------------- lease rules

def lease_rules(c: Ctx) -> None:
    S, T, M, L = c.S, c.T, c.M, c.L
    ML, MT, MU = M["leases"], M["tenants"], M["units"]
    describe = _describe_target_lease(c)
    n_target = len(T.leases)
    for s in S.leases.values():
        sc = _lease_ctx(S, s.key)
        subject = _subject(sc["tenant"], sc["unit"])
        rent = s.n.get("monthly_rent")
        keys = _keys(c, lease_key=s.key)
        s_ev = ev(s, {"tenant": sc["tenant"], "unit": sc["unit"]})
        active = s.n.get("is_active")
        if s.key in ML.matches:
            m = ML.matches[s.key]
            t = T.leases[m.t]
            tc = _lease_ctx(T, t.key)
            t_ev = ev(t, {"tenant": tc["tenant"], "unit": tc["unit"]})
            comp = _lease_compare(c, s, t)
            mi = match_info(m, "lease: unit + tenant + term")
            common = dict(source=s_ev, target=t_ev, compare=comp, match=mi, keys=keys)
            if active:
                tr = t.n.get("monthly_rent")
                if t.n.get("state") == "unknown":
                    c.add("L-004", s.key, subject=subject, **common, title="Lease status not recognized",
                          severity="medium", confidence="needs_review", pending_rent=rent or 0,
                          explanation=(f"This lease is active in {L['old']} at {_money(rent)}/month. The matching lease "
                                       f"in {L['new']} ({t.id}) has status '{t.f.get('status')}', which MergeProof "
                                       f"can't interpret, so it can't confirm the lease is still billing. Not counted "
                                       f"as rent at risk until reviewed."),
                          action="Check what this status means in the consolidated system and whether rent is billing.",
                          impact={"type": "pending_review", "cents": rent or 0, "monthly": True,
                                  "label": "Rent under review — not counted as at risk",
                                  "calc": [["Legacy monthly rent", _money(rent)],
                                           ["Consolidated status", t.f.get("status") or "blank"]]})
                elif not t.n.get("is_active"):
                    how = ("has no status column, and its dates place it as "
                           f"'{t.n.get('state')}'") if t.n.get("status_inferred") else \
                          f"has status '{t.f.get('status') or 'blank'}'"
                    c.add("L-004", s.key, subject=subject, **common,
                          explanation=(f"This lease is active in {L['old']} at {_money(rent)}/month, but the matching "
                                       f"lease in {L['new']} ({t.id}) {how}. "
                                       f"If it stays inactive, this rent may not be billed."),
                          action=(f"Confirm with the tenant/property manager whether the lease is still in force; "
                                  f"if so, reactivate it in {L['new_system']} before the next rent run."),
                          rent_risk=rent or 0, risk_key=s.id or s.key,
                          impact={"type": "rent_at_risk", "cents": rent or 0, "monthly": True,
                                  "label": "Full monthly rent may not bill",
                                  "calc": [["Expected monthly rent (legacy)", _money(rent)],
                                           ["Billed while inactive", _money(0)],
                                           ["Monthly rent at risk", _money(rent)]]})
                if rent is not None and tr is None:
                    c.add("L-002", s.key, subject=subject, **common,
                          explanation=(f"Rent is {_money(rent)}/month in {L['old']} but blank or unreadable in "
                                       f"{L['new']} ('{t.f.get('monthly_rent') or ''}')."),
                          action="Enter the correct rent in the consolidated lease and confirm the recurring charge.",
                          rent_risk=rent, risk_key=s.id or s.key, severity="critical",
                          impact={"type": "rent_at_risk", "cents": rent, "monthly": True,
                                  "label": "Rent missing after migration",
                                  "calc": [["Expected monthly rent (legacy)", _money(rent)],
                                           ["Observed monthly rent", "blank"],
                                           ["Monthly rent at risk", _money(rent)]]})
                elif rent is not None and tr is not None and tr < rent:
                    delta = rent - tr
                    sev = "critical" if delta >= 30000 or delta * 100 >= rent * 15 else "high"
                    c.add("L-002", s.key, subject=subject, **common, severity=sev,
                          explanation=(f"Monthly rent was {_money(rent)} in {L['old']} and is {_money(tr)} in "
                                       f"{L['new']} — {_money(delta)}/month lower on the same lease."),
                          action=("Check the signed lease or latest renewal. If the legacy amount is correct, fix the "
                                  "recurring charge before the next rent cycle; if the reduction is legitimate "
                                  "(concession, renewal), mark it as an expected exception with the reason."),
                          rent_risk=delta, risk_key=s.id or s.key,
                          impact={"type": "rent_at_risk", "cents": delta, "monthly": True,
                                  "label": "Possible recurring underbilling",
                                  "calc": [["Expected monthly rent (legacy)", _money(rent)],
                                           ["Observed monthly rent (consolidated)", _money(tr)],
                                           ["Monthly difference", nz.cents_to_str(-delta)]]})
                elif rent is not None and tr is not None and tr > rent:
                    delta = tr - rent
                    c.add("L-003", s.key, subject=subject, **common,
                          explanation=(f"Monthly rent is {_money(tr)} in {L['new']} versus {_money(rent)} in "
                                       f"{L['old']} — {_money(delta)}/month higher. Not counted as rent at risk, "
                                       f"but the tenant could be overbilled."),
                          action="Confirm a renewal or increase notice exists; otherwise correct the charge.",
                          impact={"type": "rent_increase", "cents": delta, "monthly": True,
                                  "label": "Higher rent — review, not counted as risk",
                                  "calc": [["Expected monthly rent (legacy)", _money(rent)],
                                           ["Observed monthly rent (consolidated)", _money(tr)],
                                           ["Monthly difference", nz.cents_to_str(delta, signed=True)]]})
                changes = [r for r in comp if r["field"] in ("start_date", "end_date") and r["state"] == "differs"]
                if changes:
                    parts = [f"{r['label'].lower()} {(r['before'] or {}).get('value') or 'blank'} → "
                             f"{(r['after'] or {}).get('value') or 'blank'}" for r in changes]
                    ends_early = (t.n.get("end_date") and c.as_of and t.n["end_date"] < c.as_of)
                    c.add("L-005", s.key, subject=subject, **common,
                          severity="high" if ends_early else "medium",
                          explanation=(f"Lease term changed during migration: {'; '.join(parts)}."
                                       + (" The new end date is already in the past, so the lease could auto-expire "
                                          "or roll to month-to-month." if ends_early else "")),
                          action="Compare against the signed lease and correct the term in the consolidated system.")
                tu, tt = T.lease_unit.get(t.key), T.lease_tenant.get(t.key)
                exp_unit = MU.matches[sc["unit_key"]].t if sc["unit_key"] in MU.matches else None
                if not (S.units and T.units):
                    if same_unit(S, T, MU, s.key, t.key) is False:
                        c.add("L-007", s.key, subject=subject, **common,
                              explanation=(f"The lease references unit '{s.f.get('unit_id')}' in {L['old']} but "
                                           f"'{t.f.get('unit_id')}' in {L['new']}."),
                              action="Confirm which unit the tenant occupies and correct the lease.")
                elif tu is None and t.n.get("unit_id"):
                    c.add("L-009", s.key, subject=subject, **common,
                          explanation=(f"The consolidated lease {t.id} points to unit reference "
                                       f"'{t.f.get('unit_id')}', which does not exist in {L['new']}'s unit list. "
                                       f"It should be {sc['unit']} ({T.unit_text(exp_unit) or 'unmatched'} after "
                                       f"migration). Rent may post without a unit, breaking occupancy and "
                                       f"unit-level reporting."),
                          action=f"Re-link lease {t.id} to {T.unit_text(exp_unit) or sc['unit']} in the consolidated system.")
                elif exp_unit and tu and tu != exp_unit:
                    c.add("L-007", s.key, subject=subject, **common,
                          explanation=(f"{sc['tenant'] or 'The tenant'}'s lease was on {sc['unit']} in {L['old']}, "
                                       f"but in {L['new']} it is attached to {T.unit_text(tu)}. "
                                       f"{T.unit_text(exp_unit)} now shows no active lease."),
                          action=(f"Move lease {t.id} to {T.unit_text(exp_unit)}; check that {T.unit_text(tu)} "
                                  f"isn't marketed as occupied or vacant incorrectly."))
                exp_ten = MT.target_set(sc["tenant_key"]) if sc["tenant_key"] else set()
                if not (S.tenants and T.tenants):
                    pass  # no tenant files: tenant references are only a matching signal
                elif tt is None and t.n.get("tenant_id"):
                    c.add("L-010", s.key, subject=subject, **common,
                          explanation=(f"The consolidated lease {t.id} references tenant '{t.f.get('tenant_id')}', "
                                       f"which does not exist in {L['new']}'s tenant list."),
                          action="Attach the lease to the correct tenant record.")
                elif sc["tenant_key"] and tt and tt not in exp_ten and (
                        exp_ten or name_similarity(S.tenants[sc["tenant_key"]].n.get("name") or "",
                                                   T.tenants[tt].n.get("name") or "") < 80):
                    # Claim a different leaseholder only on contrary evidence: the legacy tenant matched someone
                    # else, or the names clearly differ. Unverifiable contact details alone are not a finding.
                    c.add("L-008", s.key, subject=subject, **common,
                          explanation=(f"In {L['old']} this lease belongs to {sc['tenant']}; in {L['new']} the "
                                       f"matching lease belongs to {T.tenant_name(tt)}, whose contact details don't "
                                       f"confirm they are the same person."),
                          action="Confirm the leaseholder and re-link the lease to the right tenant record.")
            else:
                if t.n.get("is_active") and s.n.get("state") in ("expired", "cancelled"):
                    c.add("L-014", s.key, subject=subject, **common,
                          explanation=(f"This lease had {s.n.get('state')} in {L['old']} but is active in {L['new']}. "
                                       f"A former tenant could be billed."),
                          action="Set the consolidated lease back to its ended status.")
        elif s.key in ML.review and (active or any(cd["taken_by"] is None for cd in ML.review[s.key])):
            cands = [_candidate_view(c, "leases", cand, describe) for cand in ML.review[s.key]]
            best = cands[0]["score"] if cands else 0
            rule = "L-006"
            c.add(rule, s.key, subject=subject, source=s_ev, compare=_lease_compare(c, s, None),
                  candidates=cands, keys=keys, confidence="needs_review",
                  severity="high" if active else "low",
                  explanation=(f"{sc['tenant'] or 'This'} lease on {sc['unit']} ({_money(rent)}/month, "
                               f"{s.n.get('state')}) has {len(cands)} possible match"
                               f"{'es' if len(cands) != 1 else ''} in {L['new']}, but none is strong enough to accept "
                               f"automatically (best {best}/100; auto-match needs {TH['leases'].probable}). "
                               f"MergeProof will not call this lease missing or matched until a reviewer decides."),
                  action=("Compare the candidates below. Confirm the correct one, or leave unresolved and "
                          "investigate with the property manager."),
                  pending_rent=rent if active else 0,
                  impact={"type": "pending_review", "cents": rent if active else 0, "monthly": True,
                          "label": "Rent under review — not counted as at risk",
                          "calc": [["Legacy monthly rent", _money(rent)],
                                   ["Status", "awaiting reviewer decision"]]})
        else:
            searched = (f"Searched {n_target} {L['new_short']} leases by unit, tenant, dates and rent; "
                        f"no candidate reached the review threshold ({TH['leases'].review}/100).")
            t_tenants = MT.target_set(sc["tenant_key"]) if sc["tenant_key"] else set()
            tenant_note = ""
            if t_tenants:
                tk = sorted(t_tenants)[0]
                tenant_note = (f" {sc['tenant']} does exist in {L['new']} ({T.tenants[tk].id}) — with no lease "
                               f"attached.")
            unit_note = ""
            if sc["unit_key"] and sc["unit_key"] not in MU.matches:
                unit_note = f" The unit itself was not found in {L['new']} either."
            if active:
                c.add("L-001", s.key, subject=subject, source=s_ev, compare=_lease_compare(c, s, None), keys=keys,
                      explanation=(f"Lease at {sc['unit']} is active in {L['old']} at {_money(rent)}/month, but no "
                                   f"confidently matching lease exists in {L['new']}.{tenant_note}{unit_note} {searched}"),
                      action=(f"Locate the signed lease and recreate it in {L['new_system']} (tenant, unit, term, rent "
                              f"and recurring charges) before the next rent run. If the tenant moved out before "
                              f"cutover, mark this as an expected exception with the move-out date."),
                      rent_risk=rent or 0, risk_key=s.id or s.key,
                      impact={"type": "rent_at_risk", "cents": rent or 0, "monthly": True,
                              "label": "Full monthly rent may not bill",
                              "calc": [["Expected monthly rent (legacy)", _money(rent)],
                                       ["Matching lease after migration", "none found"],
                                       ["Monthly rent at risk", _money(rent)]]},
                      match={"status": "unmatched", "score": 0, "reasons": [], "notes": [searched], "method": ""})
            elif s.n.get("state") == "unknown":
                c.add("L-016", s.key, subject=subject, source=s_ev, compare=_lease_compare(c, s, None), keys=keys,
                      confidence="needs_review", pending_rent=rent or 0,
                      explanation=(f"A lease on {sc['unit']} ({_money(rent)}/month) has status "
                                   f"'{s.f.get('status')}' in {L['old']}, which MergeProof can't interpret, and it has "
                                   f"no match in {L['new']}. If it is current, its rent may not bill. {searched}"),
                      action="Confirm whether the lease is current; if so, recreate it in the consolidated system.",
                      impact={"type": "pending_review", "cents": rent or 0, "monthly": True,
                              "label": "Rent under review — not counted as at risk",
                              "calc": [["Legacy monthly rent", _money(rent)],
                                       ["Legacy status", s.f.get("status") or "blank"]]})
            elif s.n.get("state") == "future":
                c.add("L-015", s.key, subject=subject, source=s_ev, compare=_lease_compare(c, s, None), keys=keys,
                      explanation=(f"A signed lease starting {s.n.get('start_date')} on {sc['unit']} "
                                   f"({_money(rent)}/month) is not in {L['new']}. {searched}"),
                      action="Recreate the upcoming lease so move-in and first rent are scheduled.")
            else:
                closest = ""
                if s.key in ML.review:
                    taken = ML.review[s.key][0]
                    closest = (f" Its closest consolidated record ({T.leases[taken['pair'].t].id}) is the "
                               f"current lease, already matched to {S.leases[taken['taken_by']].id}.")
                c.add("L-012", s.key, subject=subject, source=s_ev, compare=_lease_compare(c, s, None), keys=keys,
                      explanation=(f"A {s.n.get('state') or 'non-active'} lease ({s.f.get('start_date')} → "
                                   f"{s.f.get('end_date')}) on {sc['unit']} was not carried into {L['new']}. "
                                   f"No current rent is involved; lease history may matter for deposits, "
                                   f"references or disputes.{closest}"),
                      action="Decide whether lease history must be retained; archive the legacy export if not.")

    for s_key, extras in ML.duplicates.items():
        s = S.leases[s_key]
        sc = _lease_ctx(S, s_key)
        m = ML.matches[s_key]
        rows = [ev(T.leases[m.t], describe(T.leases[m.t]))] + [ev(T.leases[p.t], describe(T.leases[p.t])) for p in extras]
        c.add("L-013", s_key, subject=_subject(sc["tenant"], sc["unit"]), source=ev(s, {"tenant": sc["tenant"],
                                                                                       "unit": sc["unit"]}),
              target=rows[0], extra_targets=rows[1:], keys=_keys(c, lease_key=s_key),
              match=match_info(m), compare=_lease_compare(c, s, T.leases[m.t]),
              explanation=(f"One legacy lease is represented by {len(rows)} leases in {L['new']} "
                           f"({', '.join(r['id'] for r in rows)}), each a strong match. Rent could be charged twice."),
              action="Keep one lease, void the duplicate, and check the tenant ledger for double charges.")

    for t_key in ML.unmatched_t:
        if t_key in ML.claimed:
            continue
        t = T.leases[t_key]
        if not t.n.get("is_active"):
            continue
        tc = _lease_ctx(T, t_key)
        started = t.n.get("start_date")
        after_cutover = bool(started and c.as_of and started > c.as_of)
        broken = []
        if T.units and T.lease_unit.get(t_key) is None and t.n.get("unit_id"):
            broken.append(f"its unit reference '{t.f.get('unit_id')}' doesn't exist")
        if T.tenants and T.lease_tenant.get(t_key) is None and t.n.get("tenant_id"):
            broken.append(f"its tenant reference '{t.f.get('tenant_id')}' doesn't exist")
        c.add("L-011", f"after:{t_key}", subject=_subject(tc["tenant"], tc["unit"]),
              target=ev(t, {"tenant": tc["tenant"], "unit": tc["unit"]}),
              severity="low" if after_cutover else "medium",
              explanation=(f"{L['new']} has an active lease ({t.id}, {_money(t.n.get('monthly_rent'))}/month"
                           f"{', starting ' + started if started else ''}) with no counterpart in {L['old']}."
                           + (" It starts after the legacy export date, so it is probably a new lease signed after "
                              "cutover." if after_cutover else
                              " It started before the legacy export was taken, so it should have appeared there.")
                           + (f" Also, {' and '.join(broken)}." if broken else "")),
              action="Confirm the lease is genuine (signed agreement, deposit received) or remove it.",
              keys={"target_lease": t_key}, confidence="unmatched",
              compare=[crow("lease_id", "Lease ID", "id", None, t.f.get("lease_id"), None, t.id, state="differs"),
                       crow("tenant", "Tenant", "text", None, tc["tenant"], state="differs"),
                       crow("unit", "Unit", "text", None, tc["unit"], state="differs"),
                       crow("start_date", "Start date", "date", None, t.f.get("start_date"), None,
                            t.n.get("start_date"), state="differs"),
                       crow("monthly_rent", "Monthly rent", "money", None, t.f.get("monthly_rent"), None,
                            t.n.get("monthly_rent"), state="differs")])


# --------------------------------------------------------------------------- tenants

def tenant_rules(c: Ctx, lease_finding_tenants: set) -> None:
    S, T, M, L = c.S, c.T, c.M, c.L
    MT = M["tenants"]
    reported: set[frozenset] = set()

    def tcompare(s: Rec | None, t: Rec) -> list[dict]:
        return [
            crow("tenant_id", "Tenant ID", "id", s.f.get("tenant_id") if s else None, t.f.get("tenant_id"),
                 s.id if s else None, t.id, state="rekeyed" if s else "differs"),
            crow("name", "Name", "name", display_name(s) if s else None, display_name(t),
                 s.n.get("name") if s else None, t.n.get("name")),
            crow("email", "Email", "email", s.f.get("email") if s else None, t.f.get("email"),
                 s.n.get("email") if s else None, t.n.get("email")),
            crow("phone", "Phone", "phone", s.f.get("phone") if s else None, t.f.get("phone"),
                 s.n.get("phone") if s else None, t.n.get("phone")),
        ]

    for s_key, extras in MT.duplicates.items():
        s = S.tenants[s_key]
        m = MT.matches[s_key]
        targets = [m.t] + [p.t for p in extras]
        reported.add(frozenset(targets))
        recs = [T.tenants[k] for k in targets]
        with_lease = [r for r in recs if T.leases_by_tenant.get(r.key)]
        lease_of = {r.key: len(T.leases_by_tenant.get(r.key, [])) for r in recs}
        lk = next((l for l in S.leases_by_tenant.get(s_key, []) if S.leases[l].n.get("is_active")), None)
        unit = S.unit_text(S.lease_unit.get(lk)) if lk else None
        c.add("T-001", s_key, subject=_subject(display_name(s), unit),
              source=ev(s, {"tenant": display_name(s)}),
              target=ev(recs[0], {"leases": lease_of[recs[0].key]}),
              extra_targets=[ev(r, {"leases": lease_of[r.key]}) for r in recs[1:]],
              compare=tcompare(s, recs[1] if len(recs) > 1 else recs[0]), match=match_info(m),
              keys=_keys(c, lease_key=lk, tenant_key=s_key),
              explanation=(f"{display_name(s)} appears {len(recs)} times in {L['new']} "
                           f"({', '.join(r.id for r in recs)}) with the same normalized email/phone. "
                           + (f"Only {', '.join(r.id for r in with_lease)} holds a lease. " if with_lease else "")
                           + "Duplicates split the tenant ledger and can cause double notices or charges."),
              action=("Merge the duplicate resident records in the consolidated system, keeping the one "
                      "attached to the lease and ledger."))

    groups = defaultdict(list)
    for t in T.tenants.values():
        if t.n.get("email"):
            groups[t.n["email"]].append(t.key)
    for email, keys_ in sorted(groups.items()):
        if len(keys_) < 2 or any(frozenset(keys_) <= r for r in reported):
            continue
        recs = [T.tenants[k] for k in sorted(keys_)]
        reported.add(frozenset(keys_))
        c.add("T-001", f"after:{email}", subject=display_name(recs[0]) or email, target=ev(recs[0]),
              extra_targets=[ev(r) for r in recs[1:]], compare=tcompare(None, recs[1]),
              explanation=(f"{len(recs)} tenant records in {L['new']} share the email {email} "
                           f"({', '.join(r.id for r in recs)}) and have no legacy counterpart."),
              action="Check whether these are the same person and merge if so.", keys={})

    for s_key in MT.review:
        s = S.tenants[s_key]
        if s_key in lease_finding_tenants:
            continue
        if not any(S.leases[l].n.get("is_active") for l in S.leases_by_tenant.get(s_key, [])):
            continue
        cands = [_candidate_view(c, "tenants", cand, lambda r: {"name": display_name(r), "email": r.f.get("email"),
                                                                "phone": r.f.get("phone")})
                 for cand in MT.review[s_key]]
        c.add("T-003", s_key, subject=display_name(s) or s.id, source=ev(s), candidates=cands,
              keys=_keys(c, tenant_key=s_key), confidence="needs_review",
              explanation=(f"{display_name(s)} has possible matches in {L['new']}, but email/phone evidence is not "
                           f"strong enough to accept one automatically."),
              action="Confirm the right tenant record or leave unresolved.")


# --------------------------------------------------------------------------- deposits

def deposit_rules(c: Ctx) -> None:
    S, T, M, L = c.S, c.T, c.M, c.L
    ML = M["leases"]

    def by_tenant_ref(side: Side) -> dict:
        idx = defaultdict(list)
        for l in side.leases.values():
            if l.n.get("tenant_id"):
                idx[l.n["tenant_id"]].append(l.key)
        return idx

    tenant_refs = {"before": by_tenant_ref(S), "after": by_tenant_ref(T)}

    def lease_of_deposit(side: Side, d: Rec) -> str | None:
        lk = side.lease_by_id.get(d.n.get("lease_id"))
        if lk:
            return lk
        tid = d.n.get("tenant_id")
        tk = side.tenant_by_id.get(tid)
        # Without a tenants export, a tenant-keyed deposit still links through the leases' own tenant references.
        ls = side.leases_by_tenant.get(tk, []) if tk else tenant_refs[side.name].get(tid or "", [])
        act = [l for l in ls if side.leases[l].n.get("is_active")] or ls
        return act[0] if act else None

    src_by_lease, tgt_by_lease = defaultdict(list), defaultdict(list)
    unlinked = {"before": [], "after": []}
    for side, bucket in ((S, src_by_lease), (T, tgt_by_lease)):
        for d in side.deposits.values():
            lk = lease_of_deposit(side, d)
            if lk:
                bucket[lk].append(d)
            elif (d.n.get("balance") or 0) > 0:
                unlinked[side.name].append(d)
    for side_name, deps in unlinked.items():
        if not deps:
            continue
        total = sum(d.n.get("balance") or 0 for d in deps)
        who = L["old"] if side_name == "before" else L["new"]
        c.add("D-004", f"{side_name}:deposits:unlinked", subject=f"{len(deps)} deposit row(s) · {who}",
              source=ev(deps[0]) if side_name == "before" else None,
              target=ev(deps[0]) if side_name == "after" else None,
              members=[{"file": d.file, "row": d.row, "id": d.id, "lease": d.f.get("lease_id"),
                        "tenant": d.f.get("tenant_id"), "balance": _money(d.n.get("balance"))} for d in deps[:200]],
              explanation=(f"{len(deps)} deposit row(s) in {who} totalling {_money(total)} reference a lease or tenant "
                           f"that can't be found, so they couldn't be reconciled. Not included in the deposit "
                           f"discrepancy total until linked."),
              action="Check the lease/tenant references on these deposit rows (or include the missing leases) and rerun.")

    def dcompare(s_deps, t_deps, lease_s, lease_t):
        s0 = s_deps[0] if s_deps else None
        t0 = t_deps[0] if t_deps else None
        sb = sum(d.n.get("balance") or 0 for d in s_deps)
        tb = sum(d.n.get("balance") or 0 for d in t_deps) if t_deps else None
        rows = [
            crow("deposit_id", "Deposit ID", "id", s0.f.get("deposit_id") if s0 else None,
                 t0.f.get("deposit_id") if t0 else None, s0.id if s0 else None, t0.id if t0 else None,
                 state=("missing" if not t0 else "rekeyed")),
            crow("lease", "Lease", "text", lease_s, lease_t, state="same" if lease_t else "missing"),
            crow("balance", "Held balance", "money", ", ".join(d.f.get("balance") or "" for d in s_deps),
                 ", ".join(d.f.get("balance") or "" for d in t_deps) if t_deps else None, sb, tb,
                 missing_after=not t_deps),
        ]
        return rows

    def blank_balance(d: Rec) -> bool:
        return "balance" in d.f and nz.blank(d.f.get("balance"))

    matched_src_tgt = set()
    for lk, deps in sorted(src_by_lease.items()):
        total = sum(d.n.get("balance") or 0 for d in deps)
        if total <= 0:
            unknown = [d for d in deps if blank_balance(d)]
            if unknown and lk in ML.matches:
                matched_src_tgt.add(ML.matches[lk].t)
            if unknown:
                s_lease = S.leases[lk]
                sc = _lease_ctx(S, lk)
                t_key = ML.matches[lk].t if lk in ML.matches else None
                tdeps = tgt_by_lease.get(t_key, []) if t_key else []
                ttotal = sum(d.n.get("balance") or 0 for d in tdeps)
                after = (f"{L['new']} shows {_money(ttotal)} for it" if tdeps and not all(map(blank_balance, tdeps))
                         else f"{L['new']} has no amount either")
                c.add("D-005", f"before:{lk}", subject=_subject(sc["tenant"], sc["unit"]),
                      source=ev(unknown[0], {"tenant": sc["tenant"], "unit": sc["unit"], "lease": s_lease.id}),
                      target=ev(tdeps[0], {"lease": T.leases[t_key].id}) if tdeps else None,
                      compare=dcompare(deps, tdeps, s_lease.id, T.leases[t_key].id if t_key else None),
                      keys=_keys(c, lease_key=lk), severity="medium",
                      explanation=(f"The deposit row for lease {s_lease.id} in {L['old']} has a blank balance. Blank is "
                                   f"not zero: the amount held is unknown, so it can't be reconciled ({after}). It is "
                                   f"not counted as a discrepancy because there is no amount to compare."),
                      action="Confirm the amount actually held against the trust-account statement and record it.")
            continue
        s_lease = S.leases[lk]
        sc = _lease_ctx(S, lk)
        subject = _subject(sc["tenant"], sc["unit"])
        keys = _keys(c, lease_key=lk)
        s_ev = ev(deps[0], {"tenant": sc["tenant"], "unit": sc["unit"], "lease": s_lease.id})
        if lk in ML.matches:
            t_lease = T.leases[ML.matches[lk].t]
            tdeps = tgt_by_lease.get(t_lease.key, [])
            matched_src_tgt.add(t_lease.key)
            ttotal = sum(d.n.get("balance") or 0 for d in tdeps)
            comp = dcompare(deps, tdeps, s_lease.id, t_lease.id)
            if tdeps and all(blank_balance(d) for d in tdeps):
                c.add("D-005", lk, subject=subject, source=s_ev, target=ev(tdeps[0], {"lease": t_lease.id}),
                      compare=comp, keys=keys, deposit=total, match=match_info(ML.matches[lk], "via matched lease"),
                      explanation=(f"{L['old']} holds a {_money(total)} security deposit for this lease. {L['new']} has "
                                   f"a deposit row for it, but the balance is blank. Blank is not zero — the "
                                   f"liability is unrecorded until an amount is entered. Tracked separately from rent."),
                      action="Enter the held amount on the consolidated deposit record and confirm it against the "
                             "trust-account ledger.",
                      impact={"type": "deposit", "cents": total, "monthly": False,
                              "label": "Deposit liability with a blank balance (separate from rent)",
                              "calc": [["Held per legacy ledger", _money(total)],
                                       ["Recorded after migration", "blank (unknown)"],
                                       ["Unrecorded liability", _money(total)]]})
            elif not tdeps:
                c.add("D-001", lk, subject=subject, source=s_ev, compare=comp, keys=keys, deposit=total,
                      match=match_info(ML.matches[lk], "via matched lease"),
                      explanation=(f"{L['old']} holds a {_money(total)} security deposit for this lease. The lease "
                                   f"migrated ({t_lease.id}) but no deposit is recorded against it in {L['new']}. "
                                   f"This is a tenant liability to reconcile — it is not rental revenue and is not "
                                   f"included in rent at risk."),
                      action=("Confirm the deposit is held in the trust/escrow account and record it on the "
                              "consolidated lease so it can be returned correctly at move-out."),
                      impact={"type": "deposit", "cents": total, "monthly": False,
                              "label": "Deposit liability unrecorded (separate from rent)",
                              "calc": [["Held per legacy ledger", _money(total)],
                                       ["Recorded after migration", _money(0)],
                                       ["Unrecorded liability", _money(total)]]})
            elif ttotal != total:
                delta = ttotal - total
                c.add("D-002", lk, subject=subject, source=s_ev,
                      target=ev(tdeps[0], {"lease": t_lease.id}), compare=comp, keys=keys, deposit=abs(delta),
                      match=match_info(ML.matches[lk], "via matched lease"),
                      severity="high" if abs(delta) >= 10000 else "medium",
                      explanation=(f"Deposit held is {_money(total)} in {L['old']} and {_money(ttotal)} in {L['new']} "
                                   f"({nz.cents_to_str(delta, signed=True)}). Deposit differences are liabilities and "
                                   f"are tracked separately from rent."),
                      action="Reconcile against the trust-account ledger and correct the consolidated balance.",
                      impact={"type": "deposit", "cents": abs(delta), "monthly": False,
                              "label": "Deposit balance discrepancy (separate from rent)",
                              "calc": [["Held per legacy ledger", _money(total)],
                                       ["Recorded after migration", _money(ttotal)],
                                       ["Difference", nz.cents_to_str(delta, signed=True)]]})
        elif lk in ML.review:
            continue  # decided together with the lease review
        else:
            lease_state = "missing" if s_lease.n.get("is_active") else "not carried over"
            c.add("D-001", lk, subject=subject, source=s_ev, compare=dcompare(deps, [], s_lease.id, None),
                  keys=keys, deposit=total,
                  explanation=(f"{L['old']} holds a {_money(total)} security deposit for lease {s_lease.id}, and the "
                               f"lease itself is {lease_state} in {L['new']}, so the deposit is unrecorded. Tracked as "
                               f"a liability, separate from the rent at risk on the lease."),
                  action="Record the deposit when the lease is recreated; confirm the trust-account balance.",
                  impact={"type": "deposit", "cents": total, "monthly": False,
                          "label": "Deposit liability unrecorded (separate from rent)",
                          "calc": [["Held per legacy ledger", _money(total)],
                                   ["Recorded after migration", _money(0)],
                                   ["Unrecorded liability", _money(total)]]})

    for tlk, tdeps in sorted(tgt_by_lease.items()):
        if tlk in matched_src_tgt or tlk not in ML.reverse:
            continue
        total = sum(d.n.get("balance") or 0 for d in tdeps)
        if total <= 0:
            continue
        s_key = ML.reverse[tlk]
        sc = _lease_ctx(S, s_key)
        c.add("D-003", f"after:{tlk}", subject=_subject(sc["tenant"], sc["unit"]),
              target=ev(tdeps[0], {"lease": T.leases[tlk].id}), keys=_keys(c, lease_key=s_key),
              compare=dcompare([], tdeps, S.leases[s_key].id, T.leases[tlk].id),
              explanation=(f"{L['new']} records a {_money(total)} deposit on this lease, but {L['old']} had no "
                           f"deposit for it."),
              action="Confirm the deposit was actually received.")


# --------------------------------------------------------------------------- maintenance

def _ai_ticket_candidates(c: Ctx, s: Rec, pool: list[str]) -> list[dict]:
    """Ask the local model which unmatched consolidated work orders might be ``s`` with a rewritten
    description. Filtered by property and date first; the result is shown for a human to confirm."""
    if c.ai is None or not pool:
        return []
    S, T, M = c.S, c.T, c.M
    sp = S.ticket_prop.get(s.key)
    exp_prop = M["properties"].matches[sp].t if sp in M["properties"].matches else None
    su = S.ticket_unit.get(s.key)
    exp_unit = M["units"].matches[su].t if su in M["units"].matches else None
    items = []
    for tk in pool:
        t = T.tickets[tk]
        tp = T.ticket_prop.get(tk)
        items.append({"key": tk, "text": t.f.get("summary") or "", "opened": t.n.get("opened_date"),
                      "same_property": (tp == exp_prop) if (tp and exp_prop) else None,
                      "same_unit": bool(exp_unit and T.ticket_unit.get(tk) == exp_unit)})
    hits = assist.ticket_suggestions(c.ai, {"text": s.f.get("summary") or "", "opened": s.n.get("opened_date")}, items)
    out = []
    for h in hits:
        t = T.tickets[h["key"]]
        where = T.unit_text(T.ticket_unit.get(t.key)) or (
            (T.props[T.ticket_prop[t.key]].f.get("name") + " (no unit)") if T.ticket_prop.get(t.key) else None)
        reasons = [[f"Similar description — semantic similarity {h['similarity']:.2f} (local AI model)", 0]]
        if h["same_unit"]:
            reasons.append(["Same unit", 0])
        elif h["same_property"]:
            reasons.append(["Same property", 0])
        if h["days"] is not None:
            reasons.append([f"Opened {h['days']} day{'s' if h['days'] != 1 else ''} apart", 0])
        summary = {"description": _clip(t.f.get("summary"), 160), "location": where, "status": t.f.get("status"),
                   "priority": t.f.get("priority"), "opened": t.n.get("opened_date")}
        out.append({"target_key": t.key, "score": round(h["similarity"] * 100), "reasons": reasons,
                    "notes": ["Rule-based evidence alone wasn't enough to pair these — AI suggestion only"],
                    "taken_by": None, "record": ev(t, summary), "summary": summary,
                    "origin": "ai", "similarity": h["similarity"]})
    return out


def maintenance_rules(c: Ctx) -> None:
    S, T, M, L = c.S, c.T, c.M, c.L
    MM = M["maintenance"]
    closed_missing = []
    # consolidated work orders nobody claimed — the only place AI suggestions may look
    pool = [k for k in MM.unmatched_t if k not in MM.claimed]
    for s in S.tickets.values():
        uk = S.ticket_unit.get(s.key)
        where = S.unit_text(uk) or (S.props[S.ticket_prop[s.key]].f.get("name") if S.ticket_prop.get(s.key) else None)
        subject = _subject(s.f.get("summary"), where)
        keys = _keys(c, unit_key=uk, property_key=S.ticket_prop.get(s.key))
        prio = s.n.get("priority") or "unknown"
        s_ev = ev(s, {"unit": where})
        is_open = s.n.get("is_open")

        def tcomp(t: Rec | None):
            if t is None:
                return [crow(k, lab, kind, s.f.get(k), None, s.n.get(k), missing_after=True)
                        for k, lab, kind in (("ticket_id", "Ticket ID", "id"), ("summary", "Issue", "text"),
                                             ("priority", "Priority", "priority"), ("status", "Status", "ticket_status"),
                                             ("opened_date", "Opened", "date"))]
            return [crow("ticket_id", "Ticket ID", "id", s.f.get("ticket_id"), t.f.get("ticket_id"), s.id, t.id,
                         state="rekeyed" if s.id != t.id else "same"),
                    crow("unit", "Location", "text", where, T.unit_text(T.ticket_unit.get(t.key)),
                         state=("same" if where == T.unit_text(T.ticket_unit.get(t.key)) else "format")
                         if MU_t(t) else "differs"),
                    crow("summary", "Issue", "text", _clip(s.f.get("summary"), 300), _clip(t.f.get("summary"), 300),
                         s.n.get("summary"), t.n.get("summary"), state=None if mapped_both(s, t, "summary") else "na"),
                    crow("priority", "Priority", "priority", s.f.get("priority"), t.f.get("priority"),
                         s.n.get("priority"), t.n.get("priority"), state=None if mapped_both(s, t, "priority") else "na"),
                    crow("status", "Status", "ticket_status", s.f.get("status"), t.f.get("status"),
                         s.n.get("status"), t.n.get("status")),
                    crow("opened_date", "Opened", "date", s.f.get("opened_date"), t.f.get("opened_date"),
                         s.n.get("opened_date"), t.n.get("opened_date"),
                         state=None if mapped_both(s, t, "opened_date") else "na")]

        def MU_t(t: Rec) -> bool:
            uk_s = S.ticket_unit.get(s.key)
            m = M["units"].matches.get(uk_s)
            return bool(m and m.t == T.ticket_unit.get(t.key))

        if s.key in MM.matches:
            m = MM.matches[s.key]
            t = T.tickets[m.t]
            common = dict(source=s_ev, target=ev(t, {"unit": T.unit_text(T.ticket_unit.get(t.key))}),
                          compare=tcomp(t), match=match_info(m), keys=keys)
            if is_open and not t.n.get("is_open"):
                c.add("M-002", s.key, subject=subject, **common,
                      severity="high" if nz.PRIORITY_RANK.get(prio, 1) >= 2 else "medium",
                      explanation=(f"This {prio} request was '{s.f.get('status')}' in {L['old']} but is "
                                   f"'{t.f.get('status')}' in {L['new']}, with no completion recorded in the legacy "
                                   f"system. The work may never be scheduled."),
                      action="Confirm with the vendor/tenant whether the work was done; reopen the work order if not.")
            if is_open and mapped_both(s, t, "priority") and prio in ("urgent", "high") \
                    and nz.PRIORITY_RANK.get(t.n.get("priority") or "unknown", 1) < nz.PRIORITY_RANK.get(prio, 1):
                c.add("M-003", s.key, subject=subject, **common,
                      explanation=(f"Priority dropped from '{s.f.get('priority')}' to '{t.f.get('priority')}' during "
                                   f"migration."),
                      action="Restore the original priority so response-time commitments are met.")
        elif s.key in MM.review:
            if not is_open:
                continue
            cands = [_candidate_view(c, "maintenance", cand, lambda r: {"summary": r.f.get("summary"),
                                                                        "status": r.f.get("status"),
                                                                        "opened": r.n.get("opened_date"),
                                                                        "unit": T.unit_text(T.ticket_unit.get(r.key))})
                     for cand in MM.review[s.key]]
            c.add("M-005", s.key, subject=subject, source=s_ev, candidates=cands, keys=keys, compare=tcomp(None),
                  confidence="needs_review",
                  explanation=(f"This open request has possible matches in {L['new']}, but none is strong enough to "
                               f"accept automatically."),
                  action="Confirm the matching work order or recreate it.")
        else:
            if is_open:
                sev = {"urgent": "critical", "high": "high"}.get(prio, "medium")
                ai_cands = _ai_ticket_candidates(c, s, pool)
                explanation = (f"An open {prio}-priority request ('{_clip(s.f.get('summary'), 200)}', opened "
                               f"{s.n.get('opened_date') or 'unknown date'}) at {where} is in {L['old']} but has "
                               f"no matching work order in {L['new']} by unit, date and description. "
                               + ("Urgent habitability issues carry legal and safety exposure if they stall. "
                                  if prio == "urgent" else ""))
                if ai_cands:
                    explanation += (f"The local AI model found {len(ai_cands)} possible counterpart"
                                    f"{'s' if len(ai_cands) != 1 else ''} whose description may have been rewritten "
                                    f"during migration. It is a suggestion only — confirm it below if it is the same "
                                    f"request.")
                c.add("M-001", s.key, subject=subject, source=s_ev, compare=tcomp(None), keys=keys, severity=sev,
                      explanation=explanation, candidates=ai_cands,
                      confidence="needs_review" if ai_cands else None,
                      action=(("Check the suggested work order first. If it is the same request, confirm it; "
                               "otherwise recreate the work order. ") if ai_cands else "")
                      + ("Recreate the work order now and contact the tenant to confirm status; "
                         "dispatch immediately if still unresolved." if prio == "urgent" else
                         "Recreate the work order and confirm status with the tenant."),
                      match={"status": "unmatched", "score": 0, "reasons": [], "method": "",
                             "notes": [f"Searched {len(T.tickets)} work orders by unit, date and description."]})
            else:
                closed_missing.append(s)
    if closed_missing:
        members = [{"id": s.id, "file": s.file, "row": s.row, "summary": s.f.get("summary"),
                    "status": s.f.get("status"), "opened": s.n.get("opened_date"),
                    "unit": S.unit_text(S.ticket_unit.get(s.key))} for s in closed_missing]
        c.add("M-004", "before:completed", subject=f"{len(members)} completed work orders",
              source=ev(closed_missing[0]), members=members,
              explanation=(f"{len(members)} completed/closed requests in {L['old']} have no counterpart in "
                           f"{L['new']}. No open work is affected; this is history only."),
              action="Decide whether maintenance history must be retained; archive the legacy export if not.")


# --------------------------------------------------------------------------- property / unit / data quality

def structure_rules(c: Ctx, dq: list[dict]) -> None:
    S, T, M, L = c.S, c.T, c.M, c.L
    MP, MU = M["properties"], M["units"]
    for s in S.props.values():
        if s.key in MP.matches:
            continue
        units = S.units_by_prop.get(s.key, [])
        active_rent = sum(S.leases[l].n.get("monthly_rent") or 0 for u in units for l in S.leases_by_unit.get(u, [])
                          if S.leases[l].n.get("is_active"))
        name = s.f.get("name") or s.f.get("address") or s.id
        if s.key in MP.review:
            cands = [_candidate_view(c, "properties", cand, lambda r: {"name": r.f.get("name"),
                                                                       "address": r.f.get("address")})
                     for cand in MP.review[s.key]]
            c.add("P-002", s.key, subject=name, source=ev(s), candidates=cands, keys={"property": s.key},
                  confidence="needs_review",
                  explanation=(f"{name} has possible matches in {L['new']}, but not strong enough to accept "
                               f"automatically. Its {len(units)} units and leases can't be checked until it's resolved."),
                  action="Confirm the matching property.")
        else:
            c.add("P-001", s.key, subject=name, source=ev(s), keys={"property": s.key},
                  severity="critical" if active_rent else "medium",
                  explanation=(f"{name} ({len(units)} units, {_money(active_rent)}/month active rent) has no matching "
                               f"property in {L['new']}. Its units and leases are reported individually."),
                  action="Check whether the property was skipped in the import batch.")
    for s in S.units.values():
        sp = S.unit_prop.get(s.key)
        if s.key in MU.matches or sp not in MP.matches:
            continue
        has_active = any(S.leases[l].n.get("is_active") for l in S.leases_by_unit.get(s.key, []))
        c.add("U-001", s.key, subject=S.unit_text(s.key) or s.id, source=ev(s), keys=_keys(c, unit_key=s.key),
              severity="high" if has_active else "medium",
              explanation=(f"{S.unit_text(s.key)} exists in {L['old']} but not in the matched property in {L['new']}."
                           + (" It has an active lease." if has_active else " It is vacant, so leasable inventory is "
                                                                            "missing.")),
              action="Create the unit (or correct its label) in the consolidated system.")
    for t in T.units.values():
        if T.props and T.unit_prop.get(t.key) is None and t.n.get("property_id"):
            c.add("U-002", f"after:{t.key}", subject=f"Unit {t.f.get('unit_label') or t.id}", target=ev(t), keys={},
                  explanation=(f"Unit {t.id} in {L['new']} references property '{t.f.get('property_id')}', which "
                               f"doesn't exist."),
                  action="Attach the unit to the right property.")

    dup = defaultdict(list)
    bad = defaultdict(list)
    for e in dq:
        if e["type"] == "duplicate_id":
            dup[(e["side"], e["entity"], e["id"])].append(e)
        elif e["type"] == "bad_value":
            bad[(e["side"], e["entity"], e["field"])].append(e)
    side_label = {"before": L["old"], "after": L["new"]}
    for (side, entity, rid), evs in sorted(dup.items()):
        c.add("Q-001", f"{side}:{entity}:{rid}", subject=f"{rid} ({entity})",
              members=[{"file": evs[0]["first"]["file"], "row": evs[0]["first"]["row"], "id": rid}] +
                      [{"file": ev_["file"], "row": ev_["row"], "id": rid} for ev_ in evs],
              explanation=(f"ID {rid} appears {len(evs) + 1} times in {side_label[side]}'s {entity}. Only the first "
                           f"row is used for matching; the others are checked as separate records."),
              action="Fix the export so each record has a unique ID, then rerun.")
    for (side, entity, fld), evs in sorted(bad.items()):
        c.add("Q-002", f"{side}:{entity}:{fld}", subject=f"{fld} · {entity}",
              severity="high" if fld in ("Monthly rent", "Held balance", "Lease status") else "medium",
              members=[{"file": e["rec"]["file"], "row": e["rec"]["row"], "id": e["rec"]["id"], "value": e["value"]}
                       for e in evs[:200]],
              explanation=(f"{len(evs)} value(s) in '{fld}' ({side_label[side]}, {entity}) couldn't be read, so those "
                           f"comparisons may be incomplete."
                           + (" Records with an unrecognized status are not treated as active, so they are excluded "
                              "from rent-at-risk until this is fixed." if "status" in fld.lower() else "")),
              action=("Correct the values in the export (e.g. numbers only for money) and rerun." if "status" not in
                      fld.lower() else "Use a standard status (Active/Current, Expired/Past, Cancelled, Future) "
                                      "or tell us the vocabulary, then rerun."))
    for side_obj, side in ((S, "before"), (T, "after")):
        if side == "after":
            continue  # consolidated broken references are reported per lease (L-009/L-010)
        if not side_obj.units:
            continue
        broken = [l for l in side_obj.leases.values() if side_obj.lease_unit.get(l.key) is None]
        if broken:
            c.add("Q-003", f"{side}:leases:unit", subject=f"{len(broken)} legacy leases",
                  members=[{"file": l.file, "row": l.row, "id": l.id, "value": l.f.get("unit_id")} for l in broken[:200]],
                  explanation=(f"{len(broken)} lease(s) in {side_label[side]} reference a unit that isn't in its unit "
                               f"file, so their unit can't be verified."),
                  action="Include the missing units in the legacy export, or check the mapping.")


# --------------------------------------------------------------------------- aggregate checks

def aggregate_checks(c: Ctx) -> list[dict]:
    S, T, M = c.S, c.T, c.M
    ML = M["leases"]
    sa = [l for l in S.leases.values() if l.n.get("is_active")]
    ta = [l for l in T.leases.values() if l.n.get("is_active")]
    s_rent = sum(l.n.get("monthly_rent") or 0 for l in sa)
    t_rent = sum(l.n.get("monthly_rent") or 0 for l in ta)
    matched = [(l, T.leases[ML.matches[l.key].t]) for l in sa if l.key in ML.matches]
    m_exp = sum(s.n.get("monthly_rent") or 0 for s, _ in matched)
    m_obs = sum((t.n.get("monthly_rent") or 0) for s, t in matched if t.n.get("is_active"))
    s_dep = sum(d.n.get("balance") or 0 for d in S.deposits.values())
    t_dep = sum(d.n.get("balance") or 0 for d in T.deposits.values())
    s_open = sum(1 for t in S.tickets.values() if t.n.get("is_open"))
    t_open = sum(1 for t in T.tickets.values() if t.n.get("is_open"))
    s_ten = len({S.lease_tenant.get(l.key) for l in sa if S.lease_tenant.get(l.key)})
    t_ten = len({T.lease_tenant.get(l.key) for l in ta if T.lease_tenant.get(l.key)})
    unaccounted = sum(1 for l in sa if l.key not in ML.matches)

    def chk(cid, label, before, after, kind, note_equal, note_diff, warn_equal=False):
        equal = before == after
        status = "pass" if equal and not warn_equal else ("warn" if equal else "fail")
        return {"id": cid, "label": label, "before": before, "after": after, "kind": kind,
                "delta": (after - before) if isinstance(before, int) and isinstance(after, int) else None,
                "status": status, "note": note_equal if equal else note_diff}

    return [
        chk("AGG-1", "Active lease count", len(sa), len(ta), "count",
            (f"Counts match — but {unaccounted} legacy active lease(s) have no confident match. Offsetting "
             f"errors can hide behind equal totals." if unaccounted else "Counts match and every active lease is matched."),
            "Active lease counts differ; see lease findings for which ones.", warn_equal=bool(unaccounted)),
        chk("AGG-2", "Total active monthly rent", s_rent, t_rent, "money",
            "Totals match.", "Portfolio totals differ. Row-level findings show where; totals alone can net out "
                             "gains against losses."),
        chk("AGG-3", "Rent on matched active leases", m_exp, m_obs, "money",
            "Every matched lease bills the same rent.", "Matched leases bill a different total — see rent findings."),
        chk("AGG-4", "Security deposits held", s_dep, t_dep, "money",
            "Deposit ledgers balance.", "Deposit ledgers differ — a liability reconciliation item, not revenue."),
        chk("AGG-5", "Open work orders", s_open, t_open, "count",
            "Open request counts match.", "Open request counts differ — see maintenance findings."),
        chk("AGG-6", "Tenants on active leases", s_ten, t_ten, "count",
            "Same number of leaseholders.", "Leaseholder counts differ — check duplicates and missing leases."),
        chk("AGG-7", "Units", len(S.units), len(T.units), "count", "Unit inventory matches.",
            "Unit inventory differs."),
        chk("AGG-8", "Properties", len(S.props), len(T.props), "count", "Property list matches.",
            "Property list differs."),
    ]


def benign_changes(c: Ctx) -> dict:
    """Count differences that normalization proved harmless across matched pairs."""
    S, T, M = c.S, c.T, c.M
    out = defaultdict(lambda: {"count": 0, "examples": []})

    def note(kind, before, after):
        o = out[kind]
        o["count"] += 1
        if len(o["examples"]) < 3 and [before, after] not in o["examples"]:
            o["examples"].append([before, after])

    fields = {
        "properties": [("address", "Address abbreviations")],
        "units": [("unit_label", "Unit label format")],
        "tenants": [("email", "Email letter case"), ("phone", "Phone punctuation")],
        "leases": [("start_date", "Date format"), ("end_date", "Date format"), ("monthly_rent", "Money format"),
                   ("status", "Status vocabulary")],
        "deposits": [],
        "maintenance": [("opened_date", "Date format"), ("priority", "Priority vocabulary"),
                        ("status", "Status vocabulary"), ("summary", "Description punctuation/case")],
    }
    srcs = {"properties": (S.props, T.props), "units": (S.units, T.units), "tenants": (S.tenants, T.tenants),
            "leases": (S.leases, T.leases), "maintenance": (S.tickets, T.tickets)}
    for ent, (sd, td) in srcs.items():
        for s_key, m in M[ent].matches.items():
            s, t = sd[s_key], td[m.t]
            if s.id and t.id and s.id != t.id:
                note("IDs re-keyed", s.id, t.id)
            for fld, kind in fields[ent]:
                sr, tr = (s.f.get(fld) or "").strip(), (t.f.get(fld) or "").strip()
                key = "state" if (ent == "leases" and fld == "status") else fld
                if sr and tr and sr != tr and s.n.get(key) == t.n.get(key) and s.n.get(key) is not None:
                    note(kind, sr, tr)
    return {k: v for k, v in sorted(out.items(), key=lambda kv: -kv[1]["count"])}


def run_all(S: Side, T: Side, M: dict[str, Result], dq: list[dict], labels: dict, as_of: str | None,
            ai=None) -> Ctx:
    c = Ctx(S, T, M, labels, as_of, ai)
    lease_rules(c)
    covered_tenants = {f["keys"].get("tenant") for f in c.findings
                       if f["rule_id"].startswith("L-") and f["keys"].get("tenant")}
    tenant_rules(c, covered_tenants)
    deposit_rules(c)
    maintenance_rules(c)
    structure_rules(c, dq)
    return c
