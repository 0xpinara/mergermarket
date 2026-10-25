"""Privacy shield for agent-facing output (MCP).

Tenant names, emails and phone numbers are replaced with stable pseudonymous tokens before anything leaves the
server through an agent tool: ``PERSON_3f9a1c2b7d``, ``EMAIL_…``, ``PHONE_…``. Tokens are HMAC-SHA256 of the
normalized value under a random per-workspace secret that is created on first use and stored only in this
server's database (``pii_secrets``). The same person gets the same token everywhere in a workspace — so an agent
can still say "the same resident appears on both leases" — and different tokens in different workspaces. There is
no detokenize tool: a person reads the real values in the app.

What is shielded: every value that appears in a tenants export (full, first and last names, emails, phones — raw
and normalized), tenant-name columns in other exports, and anything email- or phone-shaped that slipped through.
IDs, amounts, dates, file names and row numbers are left intact so evidence stays quotable."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

from . import db
from . import normalize as nz
from .ingest import norm_header

PREFIX = {"person": "PERSON", "email": "EMAIL", "phone": "PHONE"}
TOKEN_RE = re.compile(r"\b(?:PERSON|EMAIL|PHONE)_[0-9a-f]{10}\b")

EMAIL_RE = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# North American shapes — (650) 555-0104, 650-555-0104, 650.555.0104, +1 415 555 0191 — and anything that starts
# with a country code. Dates (2026-09-30, 09/30/2026), money ($2,250.00) and IDs (BV-U-0001) don't fit these.
PHONE_RE = re.compile(
    r"(?<![\w$.,/-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w/-]|\.\d)"
    r"|(?<![\w$])\+\d{1,3}(?:[\s.-]?\d){7,13}(?![\w/-]|\.\d)")

NAME_FIELDS = ("full_name", "first_name", "last_name")
_NAMEISH = ("tenant", "resident", "occupant", "renter", "lessee", "contact", "guarantor")
_PII_CACHE: dict[str, "Shield"] = {}


def workspace_secret(conn, workspace: str) -> bytes:
    row = conn.execute("SELECT secret FROM pii_secrets WHERE workspace=?", (workspace,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO pii_secrets (workspace, secret, created_at) VALUES (?,?,?)",
                     (workspace, secrets.token_hex(32), db.now()))
        row = conn.execute("SELECT secret FROM pii_secrets WHERE workspace=?", (workspace,)).fetchone()
    return bytes.fromhex(row[0])


def token(secret: bytes, kind: str, canonical: str) -> str:
    digest = hmac.new(secret, f"{kind}|{canonical}".encode(), hashlib.sha256).hexdigest()[:10]
    return f"{PREFIX[kind]}_{digest}"


def _phone_variants(digits: str) -> set[str]:
    out = {digits}
    if len(digits) == 10:
        a, b, c = digits[:3], digits[3:6], digits[6:]
        out |= {f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"{a}.{b}.{c}", f"{a} {b} {c}", f"1{digits}", f"+1{digits}",
                f"+1 {a}-{b}-{c}", f"+1 ({a}) {b}-{c}", f"1-{a}-{b}-{c}", f"+1 {a} {b} {c}"}
    return out


class Shield:
    """Replacement tables for one set of PII values under one workspace secret."""

    def __init__(self, secret: bytes, full_names: set[str], parts: set[str], emails: set[str], phones: set[str],
                 vocabulary: set[str]):
        self.secret = secret
        self.exact: dict[str, str] = {}      # case-folded whole value -> token
        whole: dict[str, str] = {}           # case-insensitive, word-bounded anywhere in text
        for full in full_names:
            canon = nz.name(full)
            if not canon or len(canon) < 3:
                continue
            t = token(secret, "person", canon)
            whole[full.casefold()] = t
            bits = full.split()
            if len(bits) >= 2:
                whole[f"{bits[-1]}, {' '.join(bits[:-1])}".casefold()] = t   # "Achebe, Rhea"
        for e in emails:
            canon = nz.email(e)
            if canon:
                t = token(secret, "email", canon)
                whole[e.strip().casefold()] = t
                whole[canon] = t
        for p in phones:
            digits = nz.phone(p)
            if digits:
                t = token(secret, "phone", digits)
                self.exact[p.strip().casefold()] = t
                for v in _phone_variants(digits):
                    whole[v.casefold()] = t
        # First/last names on their own: case-sensitive and only when the word isn't ordinary data elsewhere in
        # the deal (a street, a property, a work-order word) — full-name occurrences are always replaced.
        part_map: dict[str, str] = {}
        for part in parts:
            canon = nz.name(part)
            if not canon or len(part) < 3 or part.casefold() in vocabulary:
                continue
            t = token(secret, "person", canon)
            self.exact[part.casefold()] = t
            part_map[part] = t
        self.whole = whole
        self.part_map = part_map
        self._whole_re = self._compile(whole, re.IGNORECASE)
        self._part_re = self._compile(part_map, 0)

    @staticmethod
    def _compile(table: dict, flags: int):
        if not table:
            return None
        alts = sorted(table, key=len, reverse=True)
        return re.compile(r"(?<![\w@.+-])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![\w@-]|\.\w)", flags)

    def text(self, s: str) -> str:
        if not s:
            return s
        hit = self.exact.get(s.strip().casefold())
        if hit:
            return hit
        if self._whole_re is not None:
            s = self._whole_re.sub(lambda m: self.whole.get(m.group(0).casefold(), m.group(0)), s)
        if self._part_re is not None:
            s = self._part_re.sub(lambda m: self.part_map[m.group(0)], s)
        s = EMAIL_RE.sub(lambda m: token(self.secret, "email", m.group(0).lower()), s)
        s = PHONE_RE.sub(lambda m: token(self.secret, "phone", nz.phone(m.group(0)) or m.group(0)), s)
        return s

    def apply(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {(self.text(k) if isinstance(k, str) else k): self.apply(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.apply(v) for v in obj]
        return obj


def _collect(conn, deal_ids: list[str]) -> tuple[set, set, set, set, set, str]:
    """PII values and ordinary vocabulary for these deals, plus a cache key of what they were read from."""
    full, parts, emails, phones, vocab = set(), set(), set(), set(), set()
    basis = []
    if not deal_ids:
        return full, parts, emails, phones, vocab, ""
    marks = ",".join("?" for _ in deal_ids)
    snaps = [dict(r) for r in conn.execute(
        f"SELECT id, entity, headers_json, mapping_json, content_hash FROM snapshots WHERE deal_id IN ({marks}) "
        f"ORDER BY id", deal_ids)]
    for s in snaps:
        basis.append((s["id"], s["content_hash"], s["mapping_json"]))
        mapping = json.loads(s["mapping_json"])
        headers = json.loads(s["headers_json"])
        if s["entity"] == "tenants":
            name_cols = {f: mapping.get(f) for f in NAME_FIELDS if mapping.get(f)}
            email_col, phone_col = mapping.get("email"), mapping.get("phone")
        else:
            name_cols = {"full_name": h for h in headers
                         if any(w in norm_header(h) for w in _NAMEISH) and "name" in norm_header(h)}
            email_col = phone_col = None
        for r in conn.execute("SELECT raw_json FROM source_rows WHERE snapshot_id=?", (s["id"],)):
            raw = json.loads(r[0])
            if s["entity"] == "tenants" or name_cols:
                f = nz.text(raw.get(name_cols.get("full_name") or "", ""))
                first = nz.text(raw.get(name_cols.get("first_name") or "", ""))
                last = nz.text(raw.get(name_cols.get("last_name") or "", ""))
                for v in (f, " ".join(p for p in (first, last) if p)):
                    if v and not nz.blank(v):
                        full.add(v)
                for v in (first, last, *f.split()):
                    if v and not nz.blank(v):
                        parts.add(v)
                for col, bucket in ((email_col, emails), (phone_col, phones)):
                    if col and not nz.blank(raw.get(col)):
                        bucket.add(nz.text(raw.get(col)))
            else:
                for v in raw.values():
                    for w in re.findall(r"[A-Za-z][A-Za-z'-]+", nz.text(v)):
                        vocab.add(w.casefold())
    key = hashlib.sha256(db.dumps(basis).encode()).hexdigest()
    return full, parts, emails, phones, vocab, key


def _shield_for(conn, workspace: str, deal_ids: list[str]) -> Shield:
    secret = workspace_secret(conn, workspace)
    full, parts, emails, phones, vocab, key = _collect(conn, deal_ids)
    ck = f"{workspace}|{key}"
    if ck not in _PII_CACHE:
        if len(_PII_CACHE) > 32:
            _PII_CACHE.clear()
        _PII_CACHE[ck] = Shield(secret, full, parts, emails, phones, vocab)
    return _PII_CACHE[ck]


def shield(conn, workspace: str, deal_id: str, obj):
    """Tokenize tenant PII of one deal (plus email/phone shapes) in any JSON-able structure."""
    return _shield_for(conn, workspace, [deal_id]).apply(obj)


def shield_workspace(conn, workspace: str, obj):
    """Same, using every deal in the workspace — for outputs that span deals."""
    ids = [r[0] for r in conn.execute("SELECT id FROM deals WHERE workspace=?", (workspace,))]
    return _shield_for(conn, workspace, ids).apply(obj)
