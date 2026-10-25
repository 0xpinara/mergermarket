"""The transform allowlist. An integration plan can only ever say "apply one of these typed operations to these
source columns"; there is no expression language, no code and no model output in a transform.

Money is parsed and re-formatted, never scaled, summed or invented: `money` is the only operation allowed on a money
column, and it can't be a constant."""
from __future__ import annotations

import re
from datetime import date

from .. import normalize as nz
from .template import DATE_FORMATS, VOCABULARIES


class TransformError(ValueError):
    pass


# op -> (allowed target column types, number of source columns as (min, max), allowed params)
OPS: dict[str, dict] = {
    "copy":        {"types": {"text", "name", "key", "unit", "email", "phone"}, "sources": (1, 1), "params": set()},
    "rekey":       {"types": {"key"}, "sources": (1, 1), "params": {"prefix", "width"}},
    "ref_lookup":  {"types": {"ref"}, "sources": (1, 1), "params": {"table"}},
    "date_format": {"types": {"date"}, "sources": (1, 1), "params": {"format"}},
    "money":       {"types": {"money"}, "sources": (1, 1), "params": set()},
    "enum_map":    {"types": {"enum"}, "sources": (1, 1), "params": {"vocabulary", "values"}},
    "concat":      {"types": {"text", "name"}, "sources": (2, 4), "params": {"sep"}},
    "constant":    {"types": {"text", "enum"}, "sources": (0, 0), "params": {"value"}},
    "unit_label":  {"types": {"unit"}, "sources": (1, 1), "params": set()},
    "phone":       {"types": {"phone"}, "sources": (1, 1), "params": set()},
    "email":       {"types": {"email"}, "sources": (1, 1), "params": set()},
    "int":         {"types": {"int"}, "sources": (1, 1), "params": set()},
}

DESCRIBE = {
    "copy": "copy as text", "rekey": "issue new key", "ref_lookup": "look up new key", "date_format": "reformat date",
    "money": "parse money (exact cents)", "enum_map": "map vocabulary", "concat": "join columns",
    "constant": "fixed value", "unit_label": "normalize unit label", "phone": "normalize phone",
    "email": "normalize email", "int": "whole number",
}

SEPARATORS = {" ", ", ", " - ", ""}


def default_for(col: dict, table: dict) -> dict:
    t = col["type"]
    if t == "key":
        return {"op": "rekey", "prefix": table["key_prefix"], "width": 6} if table["key_prefix"] else {"op": "copy"}
    if t == "ref":
        return {"op": "ref_lookup", "table": col["ref"]}
    if t == "date":
        return {"op": "date_format", "format": col["format"]}
    if t == "money":
        return {"op": "money"}
    if t == "enum":
        inverse = {}
        for target, canon in col["values"].items():
            if canon is not None:
                inverse.setdefault(canon, target)
        return {"op": "enum_map", "vocabulary": col.get("vocabulary"), "values": inverse}
    return {"op": {"unit": "unit_label", "phone": "phone", "email": "email", "int": "int"}.get(t, "copy")}


def validate(col: dict, table: dict, transform: dict, sources: list[str], headers: list[str]) -> dict:
    """Validate a reviewer-supplied transform for a target column; return the normalized transform."""
    if not isinstance(transform, dict) or transform.get("op") not in OPS:
        raise TransformError(f"Unknown transform. Allowed: {', '.join(OPS)}.")
    op = transform["op"]
    spec = OPS[op]
    if col["type"] not in spec["types"]:
        raise TransformError(f"'{op}' can't produce a {col['type']} column.")
    lo, hi = spec["sources"]
    if not lo <= len(sources) <= hi:
        raise TransformError(f"'{op}' takes {lo if lo == hi else f'{lo}-{hi}'} source column(s).")
    missing = [s for s in sources if s not in headers]
    if missing:
        raise TransformError(f"Source column {missing[0]!r} is not in the legacy export.")
    extra = set(transform) - {"op"} - spec["params"]
    if extra:
        raise TransformError(f"'{op}' doesn't take {', '.join(sorted(extra))}.")
    out = {"op": op}
    if op == "rekey":
        prefix = transform.get("prefix", table["key_prefix"])
        width = transform.get("width", 6)
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Z0-9]{0,6}-?", prefix):
            raise TransformError("Key prefix must be up to 6 capitals/digits and an optional '-'.")
        if not isinstance(width, int) or not 3 <= width <= 9:
            raise TransformError("Key width must be 3-9 digits.")
        out.update(prefix=prefix, width=width)
    elif op == "ref_lookup":
        if transform.get("table", col["ref"]) != col["ref"]:
            raise TransformError(f"This column references {col['ref']}; the lookup table can't change.")
        out["table"] = col["ref"]
    elif op == "date_format":
        fmt = transform.get("format", col.get("format"))
        if fmt not in DATE_FORMATS:
            raise TransformError(f"Date format must be one of {', '.join(DATE_FORMATS)}.")
        out["format"] = fmt
    elif op == "enum_map":
        vocab = transform.get("vocabulary", col.get("vocabulary"))
        if vocab not in VOCABULARIES:
            raise TransformError("This column has no canonical vocabulary to map from.")
        values = transform.get("values")
        allowed = set(col["values"])
        if not isinstance(values, dict) or not values or any(
                not isinstance(k, str) or v not in allowed for k, v in values.items()):
            raise TransformError(f"Map canonical values onto: {', '.join(sorted(allowed))}.")
        out.update(vocabulary=vocab, values=dict(values))
    elif op == "concat":
        sep = transform.get("sep", " ")
        if sep not in SEPARATORS:
            raise TransformError("Separator must be a space, comma, dash or nothing.")
        out["sep"] = sep
    elif op == "constant":
        if col.get("canonical") is not None:
            raise TransformError("A constant can only fill a column that has no legacy equivalent.")
        value = transform.get("value")
        if not isinstance(value, str) or not 1 <= len(value) <= 60:
            raise TransformError("Constant must be 1-60 characters.")
        if col["type"] == "enum" and value not in col["values"]:
            raise TransformError(f"Constant must be one of: {', '.join(col['values'])}.")
        out["value"] = value
    return out


def _fmt_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def _fmt_phone(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else digits


def apply_value(t: dict, raw: list) -> tuple[str | None, str | None]:
    """Apply a per-value transform. Returns (output, problem). Blank in -> blank out (never zero)."""
    op = t["op"]
    if op == "constant":
        return t["value"], None
    if op == "concat":
        parts = [nz.text(v) for v in raw if not nz.blank(v)]
        return (t["sep"].join(parts) or None), None
    v = raw[0] if raw else None
    if nz.blank(v):
        return None, None
    if op == "copy":
        return nz.text(v), None
    if op == "date_format":
        iso = nz.parse_date(v)
        if iso is None:
            return None, "unreadable date"
        return date.fromisoformat(iso).strftime(DATE_FORMATS[t["format"]]), None
    if op == "money":
        try:
            cents = nz.parse_money(v)
        except nz.MoneyError:
            return None, "unreadable amount"
        return (_fmt_money(cents) if cents is not None else None), (None if cents is not None else "unreadable amount")
    if op == "enum_map":
        canon = VOCABULARIES[t["vocabulary"]](v)
        if canon in t["values"]:
            return t["values"][canon], None
        return None, f"value {nz.text(v)[:30]!r} has no target equivalent"
    if op == "unit_label":
        return nz.unit_label(v), None
    if op == "phone":
        d = nz.phone(v)
        return (_fmt_phone(d), None) if d else (None, "unreadable phone")
    if op == "email":
        e = nz.email(v)
        return (e, None) if e else (None, "unreadable email")
    if op == "int":
        i = nz.integer(v)
        return (str(i), None) if i is not None else (None, "not a whole number")
    raise TransformError(f"{op} is applied by the executor")  # rekey / ref_lookup


def describe(t: dict | None, sources: list[str]) -> str:
    if not t:
        return "—"
    op = t["op"]
    src = " + ".join(f"`{s}`" for s in sources)
    if op == "rekey":
        return f"new key {t['prefix']}{'0' * (t['width'] - 1)}1… from {src}"
    if op == "ref_lookup":
        return f"{src} → new {t['table']} key"
    if op == "date_format":
        return f"{src} as {t['format']}"
    if op == "enum_map":
        return f"{src} mapped: " + ", ".join(f"{k}→{v}" for k, v in t["values"].items())
    if op == "constant":
        return f"always “{t['value']}”"
    if op == "concat":
        return f"join {src}"
    return f"{src} · {DESCRIBE[op]}"
