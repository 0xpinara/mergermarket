"""Deterministic, explainable entity matching between the legacy ("before") and
consolidated ("after") snapshots.

Every candidate pair gets an additive score from named signals so the UI can
show exactly why two records were (or were not) paired. Assignment is strictly
one-to-one; near-ties below the confirm threshold are never auto-accepted.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz, process

from .. import normalize as nz
from .canonical import Rec, display_name


@dataclass(frozen=True)
class Th:
    confirm: int
    probable: int
    review: int
    margin: int = 10


TH = {
    "properties": Th(70, 50, 30),
    "units": Th(70, 50, 30),
    "tenants": Th(70, 50, 30),
    "leases": Th(80, 60, 35),
    "maintenance": Th(80, 60, 35),
}


@dataclass
class Pair:
    s: str
    t: str
    score: int
    reasons: list            # [[label, points], ...]
    notes: list = field(default_factory=list)


@dataclass
class Match:
    s: str
    t: str
    score: int
    status: str              # confirmed | probable | reviewer_confirmed
    reasons: list
    notes: list


@dataclass
class Result:
    entity: str
    th: Th
    matches: dict = field(default_factory=dict)       # s -> Match
    reverse: dict = field(default_factory=dict)       # t -> s
    review: dict = field(default_factory=dict)        # s -> [{"pair": Pair, "taken_by": s2|None}]
    duplicates: dict = field(default_factory=dict)    # s -> [Pair]
    claimed: set = field(default_factory=set)         # target keys explained by duplicates / review
    pairs_by_s: dict = field(default_factory=dict)
    unmatched_s: list = field(default_factory=list)
    unmatched_t: list = field(default_factory=list)
    overrides_applied: list = field(default_factory=list)
    overrides_stale: list = field(default_factory=list)

    def status_of(self, s: str) -> str:
        if s in self.matches:
            return self.matches[s].status
        if s in self.review:
            return "needs_review"
        return "unmatched"

    def target_set(self, s: str) -> set:
        out = set()
        if s in self.matches:
            out.add(self.matches[s].t)
        for p in self.duplicates.get(s, []):
            out.add(p.t)
        return out

    def counts(self) -> dict:
        c = {"confirmed": 0, "probable": 0, "reviewer_confirmed": 0, "needs_review": 0, "unmatched": 0}
        for m in self.matches.values():
            c[m.status] += 1
        c["needs_review"] = len(self.review)
        c["unmatched"] = len(self.unmatched_s) - len(self.review)
        c["target_unmatched"] = len([t for t in self.unmatched_t if t not in self.claimed])
        return c


def _finalize(pair_list: list[Pair]) -> list[Pair]:
    for p in pair_list:
        p.score = max(0, min(100, sum(pts for _, pts in p.reasons)))
    return pair_list


def assign(entity: str, pairs: list[Pair], s_keys: list[str], t_keys: list[str],
           overrides: dict[str, str] | None = None) -> Result:
    th = TH[entity]
    res = Result(entity, th)
    pairs = [p for p in pairs if p.score >= th.review or (overrides and overrides.get(p.s) == p.t)]
    by_s: dict[str, list[Pair]] = defaultdict(list)
    by_t: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by_s[p.s].append(p)
        by_t[p.t].append(p)
    for lst in list(by_s.values()) + list(by_t.values()):
        lst.sort(key=lambda p: (-p.score, p.s, p.t))
    res.pairs_by_s = by_s
    t_set, s_set = set(t_keys), set(s_keys)

    for s, t in sorted((overrides or {}).items()):
        if s in s_set and t in t_set and t not in res.reverse and s not in res.matches:
            p = next((q for q in by_s.get(s, []) if q.t == t), None)
            reasons = list(p.reasons) if p else []
            res.matches[s] = Match(s, t, p.score if p else 0, "reviewer_confirmed",
                                   reasons + [["Confirmed by a reviewer", 0]], list(p.notes) if p else [])
            res.reverse[t] = s
            res.overrides_applied.append((s, t))
        else:
            res.overrides_stale.append((s, t))

    ambiguous: set[str] = set()
    for p in sorted(pairs, key=lambda p: (-p.score, p.s, p.t)):
        if p.score < th.probable:
            break
        if p.s in res.matches or p.t in res.reverse or p.s in ambiguous:
            continue
        # Two legacy records competing for one consolidated record within the margin is ambiguous at any
        # score; one legacy record with two strong consolidated matches is reported as a duplicate instead.
        rivals_t = [q for q in by_t[p.t] if q.s != p.s and q.s not in res.matches and q.s not in ambiguous
                    and q.score >= max(th.probable, p.score - th.margin)]
        rivals = []
        if p.score < th.confirm:
            rivals = [q for q in by_s[p.s] if q.t != p.t and q.t not in res.reverse and q.score >= p.score - th.margin]
        if rivals or rivals_t:
            ambiguous.add(p.s)
            ambiguous.update(q.s for q in rivals_t)
            continue
        status = "confirmed" if p.score >= th.confirm else "probable"
        res.matches[p.s] = Match(p.s, p.t, p.score, status, p.reasons, p.notes)
        res.reverse[p.t] = p.s

    for s, m in res.matches.items():
        extras = [q for q in by_s.get(s, []) if q.t != m.t and q.t not in res.reverse and q.score >= th.confirm]
        if extras:
            res.duplicates[s] = extras
            res.claimed.update(q.t for q in extras)
    dup_claimed = set(res.claimed)

    for s in s_keys:
        if s in res.matches:
            continue
        cands = []
        for q in by_s.get(s, []):
            taken = res.reverse.get(q.t)
            if taken is None and q.t not in dup_claimed:
                cands.append({"pair": q, "taken_by": None})
            elif taken is not None and q.score >= th.probable:
                cands.append({"pair": q, "taken_by": taken})
        if cands:
            res.review[s] = cands[:3]
            res.claimed.update(c["pair"].t for c in cands[:3] if c["taken_by"] is None)
        res.unmatched_s.append(s)
    res.unmatched_t = [t for t in t_keys if t not in res.reverse]
    return res


# --------------------------------------------------------------------------- side index

def _days(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


class Side:
    def __init__(self, name: str, recs: dict[str, list[Rec]]):
        self.name = name
        self.props = {r.key: r for r in recs.get("properties", [])}
        self.units = {r.key: r for r in recs.get("units", [])}
        self.tenants = {r.key: r for r in recs.get("tenants", [])}
        self.leases = {r.key: r for r in recs.get("leases", [])}
        self.deposits = {r.key: r for r in recs.get("deposits", [])}
        self.tickets = {r.key: r for r in recs.get("maintenance", [])}
        self.prop_by_id = {r.id: r.key for r in reversed(list(self.props.values())) if r.id}
        self.prop_by_name = defaultdict(list)
        for r in self.props.values():
            for k in (r.n.get("name"), r.n.get("address")):
                if k:
                    self.prop_by_name[k].append(r.key)
        self.unit_by_id = {r.id: r.key for r in reversed(list(self.units.values())) if r.id}
        self.unit_prop = {u.key: self.resolve_property(u.n.get("property_id")) for u in self.units.values()}
        self.unit_by_prop_label = {}
        self.units_by_label = defaultdict(list)
        self.units_by_prop = defaultdict(list)
        for u in self.units.values():
            lab = u.n.get("unit_label")
            p = self.unit_prop[u.key]
            if p:
                self.units_by_prop[p].append(u.key)
            if lab:
                self.unit_by_prop_label.setdefault((p, lab), u.key)
                self.units_by_label[lab].append(u.key)
        self.tenant_by_id = {r.id: r.key for r in reversed(list(self.tenants.values())) if r.id}
        self.lease_by_id = {r.id: r.key for r in reversed(list(self.leases.values())) if r.id}
        self.lease_unit = {l.key: self.resolve_unit(l.n.get("unit_id"), l.n.get("property_id"), l.f.get("unit_id"))
                           for l in self.leases.values()}
        self.lease_tenant = {l.key: self.tenant_by_id.get(l.n.get("tenant_id")) for l in self.leases.values()}
        self.leases_by_unit = defaultdict(list)
        self.leases_by_tenant = defaultdict(list)
        for k, u in self.lease_unit.items():
            if u:
                self.leases_by_unit[u].append(k)
        for k, t in self.lease_tenant.items():
            if t:
                self.leases_by_tenant[t].append(k)
        self.ticket_unit = {t.key: self.resolve_unit(t.n.get("unit_id"), t.n.get("property_id"), t.f.get("unit_id"))
                            for t in self.tickets.values()}
        self.ticket_prop = {}
        for t in self.tickets.values():
            p = self.resolve_property(t.n.get("property_id"))
            if not p and self.ticket_unit.get(t.key):
                p = self.unit_prop.get(self.ticket_unit[t.key])
            self.ticket_prop[t.key] = p

    def resolve_property(self, ref: str | None) -> str | None:
        if not ref:
            return None
        if ref in self.prop_by_id:
            return self.prop_by_id[ref]
        for k in (nz.name(ref), nz.address(ref)):
            if k and len(self.prop_by_name.get(k, [])) == 1:
                return self.prop_by_name[k][0]
        return None

    def resolve_unit(self, ref: str | None, prop_ref: str | None = None, raw: str | None = None) -> str | None:
        if not ref:
            return None
        if ref in self.unit_by_id:
            return self.unit_by_id[ref]
        lab = nz.unit_label(raw or ref)
        prop = self.resolve_property(prop_ref)
        if prop and (prop, lab) in self.unit_by_prop_label:
            return self.unit_by_prop_label[(prop, lab)]
        if not prop and lab and len(self.units_by_label.get(lab, [])) == 1:
            return self.units_by_label[lab][0]
        return None

    def unit_text(self, unit_key: str | None) -> str | None:
        if not unit_key:
            return None
        u = self.units[unit_key]
        p = self.props.get(self.unit_prop.get(unit_key) or "")
        pname = (p.f.get("name") or p.f.get("address") or p.id) if p else None
        return f"Unit {u.f.get('unit_label') or u.id}" + (f" · {pname}" if pname else "")

    def tenant_name(self, tenant_key: str | None) -> str | None:
        return display_name(self.tenants.get(tenant_key or ""))


def _ref_overlap(a: set, b: set) -> bool:
    return bool(a) and bool(b) and len(a & b) / min(len(a), len(b)) >= 0.5


def property_refs_comparable(S: Side, T: Side) -> bool:
    """Do both sides' leases name properties the same way? Re-keyed IDs (BV-P-01 vs CPM-B080) don't."""
    cache = T.__dict__.setdefault("_prop_cmp", {})
    if id(S) not in cache:
        a = {nz.name(l.f.get("property_id")) for l in S.leases.values() if l.f.get("property_id")}
        b = {nz.name(l.f.get("property_id")) for l in T.leases.values() if l.f.get("property_id")}
        cache[id(S)] = _ref_overlap(a, b)
    return cache[id(S)]


def same_property_ref(S: Side, T: Side, props: Result | None, s_ref: str | None, t_ref: str | None) -> bool | None:
    if not s_ref or not t_ref:
        return None
    sp, tp = S.resolve_property(nz.ident(s_ref)), T.resolve_property(nz.ident(t_ref))
    if props is not None and sp and tp and sp in props.matches:
        return props.matches[sp].t == tp
    if property_refs_comparable(S, T):
        return nz.name(s_ref) == nz.name(t_ref)
    return None


def same_unit(S: Side, T: Side, units: Result, s_key: str, t_key: str, props: Result | None = None) -> bool | None:
    """Is target lease t on the unit legacy lease s occupied? None when it can't be told.
    Falls back to the leases' own unit references when a side has no units export."""
    if S.units and T.units:
        su, tu = S.lease_unit.get(s_key), T.lease_unit.get(t_key)
        exp = units.matches[su].t if su in units.matches else None
        if exp is None or tu is None:
            return None
        return exp == tu
    s, t = S.leases[s_key], T.leases[t_key]
    sl, tl = nz.unit_label(s.f.get("unit_id")), nz.unit_label(t.f.get("unit_id"))
    if not sl or not tl:
        return None
    if same_property_ref(S, T, props, s.f.get("property_id"), t.f.get("property_id")) is False:
        return False
    return sl == tl


def same_tenant_ref(S: Side, T: Side, s_key: str, t_key: str) -> bool | None:
    """Fallback when a side has no tenants export: compare the leases' tenant reference text."""
    if S.tenants and T.tenants:
        return None
    a, b = nz.name(S.leases[s_key].f.get("tenant_id")), nz.name(T.leases[t_key].f.get("tenant_id"))
    if not a or not b:
        return None
    return a == b


def _id_overlap(src: dict, tgt: dict) -> bool:
    s_ids = {r.id for r in src.values() if r.id}
    t_ids = {r.id for r in tgt.values() if r.id}
    return bool(s_ids) and len(s_ids & t_ids) / len(s_ids) >= 0.5


# --------------------------------------------------------------------------- matchers

def match_properties(S: Side, T: Side, overrides: dict) -> Result:
    same_ids = _id_overlap(S.props, T.props)
    pairs = []
    for s in S.props.values():
        for t in T.props.values():
            r = []
            if t.n.get("legacy_ref") and t.n["legacy_ref"] == s.id:
                r.append(["Consolidated record's legacy reference equals the legacy property ID", 100])
            if same_ids and s.id and s.id == t.id:
                r.append(["Same property ID", 60])
            sa, ta = s.n.get("address"), t.n.get("address")
            if sa and ta:
                if sa == ta:
                    r.append(["Same street address (after normalizing abbreviations)", 60])
                else:
                    ratio = fuzz.token_sort_ratio(sa, ta)
                    if ratio >= 92:
                        r.append([f"Very similar street address ({ratio:.0f}%)", 40])
                    elif ratio >= 80:
                        r.append([f"Similar street address ({ratio:.0f}%)", 20])
            sn, tn = s.n.get("name"), t.n.get("name")
            if sn and tn:
                if sn == tn:
                    r.append(["Same property name", 30])
                elif fuzz.token_sort_ratio(sn, tn) >= 90:
                    r.append(["Very similar property name", 15])
            if s.n.get("city") and s.n.get("city", "").lower() == (t.n.get("city") or "").lower():
                r.append(["Same city", 5])
            if r:
                pairs.append(Pair(s.key, t.key, 0, r))
    return assign("properties", _finalize(pairs), list(S.props), list(T.props), overrides)


def match_units(S: Side, T: Side, props: Result, overrides: dict) -> Result:
    same_ids = _id_overlap(S.units, T.units)
    no_props = not (S.props and T.props)
    if no_props:
        a = {nz.name(u.f.get("property_id")) for u in S.units.values() if u.f.get("property_id")}
        b = {nz.name(u.f.get("property_id")) for u in T.units.values() if u.f.get("property_id")}
        prop_text = _ref_overlap(a, b)
    pairs = []
    for s in S.units.values():
        if no_props:
            # No property exports: candidates share the unit label; property refs count only if both sides use
            # the same scheme, otherwise repeated labels (1A in every building) are left for review.
            cands = T.units_by_label.get(s.n.get("unit_label") or "", [])
            if same_ids and s.id in T.unit_by_id:
                cands = list(dict.fromkeys(cands + [T.unit_by_id[s.id]]))
        else:
            sp = S.unit_prop.get(s.key)
            tp = props.matches[sp].t if sp in props.matches else None
            if not tp:
                continue
            cands = T.units_by_prop.get(tp, [])
        for tk in cands:
            t = T.units[tk]
            r = []
            if t.n.get("legacy_ref") and t.n["legacy_ref"] == s.id:
                r.append(["Legacy reference equals the legacy unit ID", 100])
            if same_ids and s.id and s.id == t.id:
                r.append(["Same unit ID", 40])
            sl, tl = s.n.get("unit_label"), t.n.get("unit_label")
            if sl and tl and sl == tl:
                same_raw = (s.f.get("unit_label") or "").strip().upper() == (t.f.get("unit_label") or "").strip().upper()
                r.append(["Same unit number in the matched property" if same_raw else
                          f"Same unit number after normalizing ('{s.f.get('unit_label')}' ≈ '{t.f.get('unit_label')}')", 70])
            notes = []
            if no_props and prop_text and s.f.get("property_id") and t.f.get("property_id"):
                if nz.name(s.f["property_id"]) == nz.name(t.f["property_id"]):
                    r.append(["Same property reference", 20])
                else:
                    continue
            sb, tb = s.n.get("bedrooms"), t.n.get("bedrooms")
            if sb is not None and tb is not None:
                if sb == tb:
                    r.append(["Same bedroom count", 10])
                else:
                    notes.append(f"Bedrooms differ ({sb} vs {tb})")
            if r:
                pairs.append(Pair(s.key, t.key, 0, r, notes))
    return assign("units", _finalize(pairs), list(S.units), list(T.units), overrides)


def match_tenants(S: Side, T: Side, overrides: dict) -> Result:
    same_ids = _id_overlap(S.tenants, T.tenants)
    by_email, by_phone = defaultdict(list), defaultdict(list)
    for t in T.tenants.values():
        if t.n.get("email"):
            by_email[t.n["email"]].append(t.key)
        if t.n.get("phone"):
            by_phone[t.n["phone"]].append(t.key)
    t_names = {t.key: t.n.get("name") for t in T.tenants.values() if t.n.get("name")}
    legacy = defaultdict(list)
    for t in T.tenants.values():
        if t.n.get("legacy_ref"):
            legacy[t.n["legacy_ref"]].append(t.key)
    pairs = []
    for s in S.tenants.values():
        cands = set(by_email.get(s.n.get("email") or "", [])) | set(by_phone.get(s.n.get("phone") or "", []))
        cands |= set(legacy.get(s.id or "", []))
        if same_ids and s.id in T.tenant_by_id:
            cands.add(T.tenant_by_id[s.id])
        if s.n.get("name") and t_names:
            for _, sc, key in process.extract(s.n["name"], t_names, scorer=fuzz.token_sort_ratio,
                                              score_cutoff=80, limit=8):
                cands.add(key)
        for tk in sorted(cands):
            t = T.tenants[tk]
            r, notes = [], []
            if t.n.get("legacy_ref") and t.n["legacy_ref"] == s.id:
                r.append(["Legacy reference equals the legacy tenant ID", 100])
            id_hit = same_ids and s.id and s.id == t.id
            if id_hit:
                r.append(["Same tenant ID", 40])
            se, te = s.n.get("email"), t.n.get("email")
            sp, tp = s.n.get("phone"), t.n.get("phone")
            if se and te:
                if se == te:
                    r.append(["Same email address (case-insensitive)", 50])
                else:
                    notes.append("Email differs")
            if sp and tp:
                if sp == tp:
                    r.append(["Same phone number (digits only)", 30])
                else:
                    notes.append("Phone differs")
            sn, tn = s.n.get("name"), t.n.get("name")
            if sn and tn:
                ratio = fuzz.token_sort_ratio(sn, tn)
                if sn == tn:
                    r.append(["Same name", 20])
                elif ratio >= 90:
                    r.append([f"Very similar name ({ratio:.0f}%)", 12])
                elif ratio >= 80:
                    r.append([f"Similar name ({ratio:.0f}%)", 5])
                else:
                    notes.append("Name differs")
            p = Pair(s.key, t.key, 0, r, notes)
            _finalize([p])
            if "Email differs" in notes and "Phone differs" in notes and not id_hit and p.score < 100:
                p.notes.append("Contact details conflict — a matching name alone is not enough")
                p.score = min(p.score, 20)
            if r:
                pairs.append(p)
    return assign("tenants", pairs, list(S.tenants), list(T.tenants), overrides)


def match_leases(S: Side, T: Side, units: Result, tenants: Result, overrides: dict,
                 props: Result | None = None) -> Result:
    same_ids = _id_overlap(S.leases, T.leases)
    t_names = {t.key: t.n.get("name") for t in T.tenants.values() if t.n.get("name")}
    legacy = defaultdict(list)
    for t in T.leases.values():
        if t.n.get("legacy_ref"):
            legacy[t.n["legacy_ref"]].append(t.key)
    unit_text = not (S.units and T.units)
    tenant_text = not (S.tenants and T.tenants)

    def mapped(side: Side, field: str) -> bool:
        return any(field in l.f for l in side.leases.values())

    # Points the exports can support at all. Weights mirror the signals below; an export without tenant or date
    # columns shouldn't make every pair look weak, so scores are expressed against what's available.
    tenant_pts = 35 if not tenant_text else (20 if mapped(S, "tenant_id") and mapped(T, "tenant_id") else 0)
    available = (40 + tenant_pts + (15 if mapped(S, "start_date") and mapped(T, "start_date") else 0)
                 + (5 if mapped(S, "end_date") and mapped(T, "end_date") else 0) + 5)
    missing_cols = [n for n, ok in (("tenant", tenant_pts), ("start date", mapped(S, "start_date") and mapped(T, "start_date")),
                                    ("end date", mapped(S, "end_date") and mapped(T, "end_date"))) if not ok]
    # In text mode a unit label repeated across properties is only a weak signal unless property refs line up.
    label_props: dict[tuple[str, str], set] = defaultdict(set)
    if unit_text:
        for side in (S, T):
            for l in side.leases.values():
                lab = nz.unit_label(l.f.get("unit_id"))
                if lab:
                    label_props[(side.name, lab)].add(nz.name(l.f.get("property_id")) or "")
    comparable_props = unit_text and property_refs_comparable(S, T)
    by_unit_ref, by_tenant_ref = defaultdict(list), defaultdict(list)
    for t in T.leases.values():
        if unit_text and nz.unit_label(t.f.get("unit_id")):
            by_unit_ref[nz.unit_label(t.f.get("unit_id"))].append(t.key)
        if tenant_text and nz.name(t.f.get("tenant_id")):
            by_tenant_ref[nz.name(t.f.get("tenant_id"))].append(t.key)
    pairs = []
    for s in S.leases.values():
        su = S.lease_unit.get(s.key)
        exp_unit = units.matches[su].t if su in units.matches else None
        st = S.lease_tenant.get(s.key)
        exp_tenants = tenants.target_set(st) if st else set()
        t_status = tenants.status_of(st) if st else None
        s_name = S.tenants[st].n.get("name") if st else None
        similar_tenants = set()
        if s_name and t_names:
            similar_tenants = {k for _, sc, k in process.extract(s_name, t_names, scorer=fuzz.token_sort_ratio,
                                                                 score_cutoff=90, limit=8)}
        cands = set(T.leases_by_unit.get(exp_unit, [])) if exp_unit else set()
        for tk in exp_tenants | similar_tenants:
            cands |= set(T.leases_by_tenant.get(tk, []))
        cands |= set(legacy.get(s.id or "", []))
        if same_ids and s.id in T.lease_by_id:
            cands.add(T.lease_by_id[s.id])
        if unit_text:
            cands |= set(by_unit_ref.get(nz.unit_label(s.f.get("unit_id")) or "", []))
        if tenant_text:
            cands |= set(by_tenant_ref.get(nz.name(s.f.get("tenant_id")) or "", []))
        for lk in sorted(cands):
            t = T.leases[lk]
            r, notes = [], []
            if t.n.get("legacy_ref") and t.n["legacy_ref"] == s.id:
                r.append(["Legacy reference equals the legacy lease ID", 100])
            if same_ids and s.id and s.id == t.id:
                r.append(["Same lease ID", 40])
            tu = T.lease_unit.get(lk)
            if unit_text:
                su_eq = same_unit(S, T, units, s.key, lk, props)
                lab = nz.unit_label(s.f.get("unit_id")) or ""
                repeated = (len(label_props[("before", lab)]) > 1 or len(label_props[("after", lab)]) > 1)
                if su_eq and repeated and not comparable_props:
                    r.append([f"Same unit label ('{s.f.get('unit_id')}'), but it appears in several properties "
                              f"whose IDs differ between systems", 20])
                elif su_eq:
                    r.append([f"Same unit reference ('{s.f.get('unit_id')}' ≈ '{t.f.get('unit_id')}')", 40])
                elif su_eq is False:
                    notes.append(f"Different unit reference ('{t.f.get('unit_id')}')")
            elif tu is None and t.n.get("unit_id"):
                notes.append(f"Consolidated unit reference '{t.f.get('unit_id')}' does not resolve to a unit")
            elif exp_unit and tu == exp_unit:
                r.append([f"Same unit ({T.unit_text(tu)})", 40])
            elif tu:
                notes.append(f"Different unit ({T.unit_text(tu)})")
            tt = T.lease_tenant.get(lk)
            if tenant_text and same_tenant_ref(S, T, s.key, lk):
                r.append([f"Same tenant reference ('{t.f.get('tenant_id')}')", 20])
            elif tt and tt in exp_tenants:
                pts = 35 if t_status in ("confirmed", "reviewer_confirmed") else 28
                r.append([f"Same tenant ({T.tenant_name(tt)}, {t_status.replace('_', ' ')} identity match)", pts])
            elif tt and tt in similar_tenants:
                r.append([f"Similar tenant name only ({T.tenant_name(tt)}) — not sufficient on its own", 12])
                notes.append("Tenant identity not confirmed by email or phone")
            elif tt:
                notes.append(f"Different tenant ({T.tenant_name(tt)})")
            elif t.n.get("tenant_id") and not tenant_text:
                notes.append(f"Consolidated tenant reference '{t.f.get('tenant_id')}' does not resolve")
            d = _days(s.n.get("start_date"), t.n.get("start_date"))
            if d == 0:
                r.append(["Same start date", 15])
            elif d is not None and d <= 31:
                r.append([f"Start date within {d} days", 7])
            elif d is not None:
                notes.append(f"Start date differs by {d} days")
            if s.n.get("end_date") and s.n.get("end_date") == t.n.get("end_date"):
                r.append(["Same end date", 5])
            if s.n.get("monthly_rent") is not None and s.n.get("monthly_rent") == t.n.get("monthly_rent"):
                r.append(["Same monthly rent", 5])
            if r:
                pairs.append(Pair(s.key, t.key, 0, r, notes))
    _finalize(pairs)
    if available < 100:
        for p in pairs:
            p.score = min(100, round(p.score * 100 / available))
            p.notes.append(f"Scored against the {available} points these exports support "
                           f"(no {', '.join(missing_cols)} on both sides)")
    return assign("leases", pairs, list(S.leases), list(T.leases), overrides)


def corroborate_tenants(S: Side, T: Side, tenants: Result, leases: Result) -> int:
    """Second pass: accept a tenant pair the contact details couldn't settle (blank email/phone) when the
    names agree and the tenant holds the lease that matched on unit, dates and rent. Returns pairs added."""
    added = 0
    for s_lease, m in sorted(leases.matches.items()):
        st, tt = S.lease_tenant.get(s_lease), T.lease_tenant.get(m.t)
        if not st or not tt or st in tenants.matches or tt in tenants.reverse or tt in tenants.claimed:
            continue
        sn, tn = S.tenants[st].n.get("name"), T.tenants[tt].n.get("name")
        if not sn or not tn:
            continue
        ratio = fuzz.token_sort_ratio(sn, tn)
        if ratio < 90:
            continue
        reasons = [["Same name" if sn == tn else f"Very similar name ({ratio:.0f}%)", 20],
                   [f"Holds the matching lease ({T.unit_text(T.lease_unit.get(m.t)) or m.t}, {m.status})", 30]]
        tenants.matches[st] = Match(st, tt, 50, "probable", reasons,
                                    ["Email/phone couldn't confirm identity; corroborated by the lease"])
        tenants.reverse[tt] = st
        tenants.review.pop(st, None)
        if st in tenants.unmatched_s:
            tenants.unmatched_s.remove(st)
        if tt in tenants.unmatched_t:
            tenants.unmatched_t.remove(tt)
        added += 1
    return added


def match_tickets(S: Side, T: Side, props: Result, units: Result, overrides: dict) -> Result:
    same_ids = _id_overlap(S.tickets, T.tickets)
    by_unit, by_prop = defaultdict(list), defaultdict(list)
    for k in T.tickets:
        if T.ticket_unit.get(k):
            by_unit[T.ticket_unit[k]].append(k)
        if T.ticket_prop.get(k):
            by_prop[T.ticket_prop[k]].append(k)
    legacy = defaultdict(list)
    for t in T.tickets.values():
        if t.n.get("legacy_ref"):
            legacy[t.n["legacy_ref"]].append(t.key)
    pairs = []
    for s in S.tickets.values():
        su, sp = S.ticket_unit.get(s.key), S.ticket_prop.get(s.key)
        eu = units.matches[su].t if su in units.matches else None
        ep = props.matches[sp].t if sp in props.matches else None
        cands = set(by_unit.get(eu, [])) if eu else set()
        if ep and not eu:
            cands |= set(by_prop.get(ep, []))
        cands |= set(legacy.get(s.id or "", []))
        if same_ids and s.id in {t.id for t in T.tickets.values()}:
            cands |= {t.key for t in T.tickets.values() if t.id == s.id}
        for tk in sorted(cands):
            t = T.tickets[tk]
            r, notes = [], []
            if t.n.get("legacy_ref") and t.n["legacy_ref"] == s.id:
                r.append(["Legacy reference equals the legacy ticket ID", 100])
            if same_ids and s.id and s.id == t.id:
                r.append(["Same ticket ID", 40])
            tu, tp = T.ticket_unit.get(tk), T.ticket_prop.get(tk)
            if eu and tu == eu:
                r.append([f"Same unit ({T.unit_text(tu)})", 40])
            elif ep and tp == ep:
                r.append(["Same property", 20])
            d = _days(s.n.get("opened_date"), t.n.get("opened_date"))
            if d == 0:
                r.append(["Same opened date", 25])
            elif d is not None and d <= 3:
                r.append([f"Opened within {d} days", 10])
            ss, ts = s.n.get("summary"), t.n.get("summary")
            if ss and ts:
                ratio = fuzz.token_set_ratio(ss, ts)
                if ratio >= 90:
                    r.append(["Same issue description", 35])
                elif ratio >= 75:
                    r.append([f"Similar issue description ({ratio:.0f}%)", 20])
                elif ratio >= 60:
                    r.append([f"Loosely similar description ({ratio:.0f}%)", 8])
                else:
                    notes.append("Different issue description")
            if r:
                pairs.append(Pair(s.key, t.key, 0, r, notes))
    return assign("maintenance", _finalize(pairs), list(S.tickets), list(T.tickets), overrides)
