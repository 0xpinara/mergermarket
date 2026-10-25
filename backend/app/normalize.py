"""Value normalizers. Every function keeps raw input untouched and returns a
comparison-ready value (or None when the input is blank/unparseable)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_WS = re.compile(r"\s+")


def text(v) -> str:
    if v is None:
        return ""
    return _WS.sub(" ", str(v)).strip()


def blank(v) -> bool:
    return text(v) == "" or text(v).lower() in {"null", "none", "n/a", "na", "-", "--"}


def ident(v) -> str | None:
    s = text(v)
    if blank(s):
        return None
    if re.fullmatch(r"\d+\.0", s):  # spreadsheet integers read as floats
        s = s[:-2]
    return s.upper()


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def name(v) -> str | None:
    s = text(v)
    if blank(s):
        return None
    s = _fold(s).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return _WS.sub(" ", s).strip() or None


def email(v) -> str | None:
    s = text(v).lower()
    if blank(s) or "@" not in s:
        return None
    s = s.replace("mailto:", "")
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None


def phone(v) -> str | None:
    digits = re.sub(r"\D", "", text(v))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else None


_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%d-%b-%Y", "%d %b %Y",
    "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y", "%Y%m%d", "%m.%d.%Y",
)


def parse_date(v) -> str | None:
    """Return ISO yyyy-mm-dd. Slash dates are read as US month/day/year."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:  # Excel serial
        return (date(1899, 12, 30) + timedelta(days=int(float(v)))).isoformat()
    s = text(v)
    if blank(s):
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", s) and 20000 < float(s) < 80000:  # Excel serial stored as a number
        return (date(1899, 12, 30) + timedelta(days=int(float(s)))).isoformat()
    s = re.sub(r"[T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", "", s)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class MoneyError(ValueError):
    pass


MAX_CENTS = 100_000_000_000  # $1B per value — anything larger is a data error, not rent


def _number_text(s: str, raw) -> str:
    """Resolve thousands separators vs decimal commas; refuse anything ambiguous."""
    if "," not in s:
        if re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", s):  # 1.250.000 (European thousands)
            return s.replace(".", "")
        return s
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):  # 1,250.00
        return s.replace(",", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,2}", s):  # 1.250,00
        return s.replace(".", "").replace(",", ".")
    if re.fullmatch(r"\d+,\d{1,2}", s):  # 2400,00
        return s.replace(",", ".")
    raise MoneyError(f"ambiguous money value: {text(raw)[:40]!r}")


def parse_money(v) -> int | None:
    """Parse a currency value to integer cents using Decimal. Raises MoneyError if
    a non-blank value cannot be read as money."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        d = Decimal(str(v))
    else:
        s = text(v)
        if blank(s):
            return None
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        s = re.sub(r"(?i)\b(usd|us\$|dollars?)\b", "", s)
        s = s.replace("$", "").replace(" ", "")
        if s.endswith("-"):
            neg, s = True, s[:-1]
        sign = ""
        if s[:1] in "+-":
            sign, s = s[0], s[1:]
        s = sign + _number_text(s, v)
        if not re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
            raise MoneyError(f"not a money value: {text(v)[:40]!r}")
        try:
            d = Decimal(s)
        except InvalidOperation as exc:  # pragma: no cover - regex guards this
            raise MoneyError(str(exc)) from exc
        if neg:
            d = -d
    if not d.is_finite() or abs(d) * 100 > MAX_CENTS:
        raise MoneyError(f"implausible money value: {text(v)[:40]!r}")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_str(c: int | None, signed: bool = False) -> str:
    if c is None:
        return "—"
    sign = "−" if c < 0 else ("+" if signed and c > 0 else "")
    return f"{sign}${abs(c) / 100:,.2f}"


_ADDR = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd",
    "drive": "dr", "court": "ct", "lane": "ln", "place": "pl", "terrace": "ter", "parkway": "pkwy",
    "highway": "hwy", "circle": "cir", "square": "sq", "way": "way", "north": "n", "south": "s",
    "east": "e", "west": "w", "apartment": "apt", "suite": "ste", "row": "row",
}


def address(v) -> str | None:
    s = text(v)
    if blank(s):
        return None
    s = _fold(s).lower()
    s = re.sub(r"[.,#]", " ", s)
    tokens = [_ADDR.get(t, t) for t in _WS.split(s) if t]
    return " ".join(tokens) or None


def unit_label(v) -> str | None:
    s = text(v).upper()
    if blank(s):
        return None
    s = re.sub(r"^(UNIT|APARTMENT|APT|SUITE|STE|NUMBER|NO)(?![A-Z])\.?\s*", "", s)
    s = re.sub(r"[#\s\-_.]", "", s)
    s = re.sub(r"^0+(?=\d)", "", s)
    return s or None


def floor_of(label: str | None) -> int | None:
    """Best-effort floor from a normalized unit label: 4B -> 4, 12C -> 12, 402 -> 4."""
    m = re.fullmatch(r"(\d+)([A-Z]*)", label or "")
    if not m:
        return None
    digits, letters = m.groups()
    if letters:
        return int(digits)
    if len(digits) >= 3:
        return int(digits[:-2])
    return 1


LEASE_STATUS = {
    "active": "active", "current": "active", "occupied": "active", "in force": "active", "inforce": "active",
    "month to month": "active", "month-to-month": "active", "mtm": "active", "notice": "active",
    "on notice": "active", "renewed": "active", "live": "active",
    "expired": "expired", "past": "expired", "ended": "expired", "former": "expired", "terminated": "expired",
    "moved out": "expired", "closed": "expired", "inactive": "expired",
    "cancelled": "cancelled", "canceled": "cancelled", "void": "cancelled", "voided": "cancelled",
    "future": "future", "pending": "future", "upcoming": "future", "approved": "future",
}

TICKET_STATUS = {
    "open": "open", "new": "open", "submitted": "open", "received": "open", "reported": "open",
    "in progress": "in_progress", "in-progress": "in_progress", "in_progress": "in_progress", "assigned": "in_progress",
    "scheduled": "in_progress", "on hold": "in_progress", "waiting": "in_progress", "pending": "in_progress",
    "completed": "closed", "complete": "closed", "closed": "closed", "resolved": "closed", "done": "closed",
    "fixed": "closed", "cancelled": "cancelled", "canceled": "cancelled", "void": "cancelled",
}

PRIORITY = {
    "emergency": "urgent", "urgent": "urgent", "critical": "urgent", "p1": "urgent", "asap": "urgent",
    "high": "high", "p2": "high", "important": "high",
    "medium": "medium", "normal": "medium", "standard": "medium", "routine": "medium", "p3": "medium",
    "low": "low", "p4": "low", "minor": "low",
}

PRIORITY_RANK = {"urgent": 3, "high": 2, "medium": 1, "low": 0, "unknown": 1}


def _vocab(v, table: dict) -> str | None:
    s = text(v).lower().replace("_", " ")
    if blank(s):
        return None
    return table.get(s) or table.get(s.replace(" ", "")) or table.get(s.replace("-", " ")) or "unknown"


def lease_status(v) -> str | None:
    out = _vocab(v, LEASE_STATUS)
    if out != "unknown":
        return out
    s = text(v).lower()
    # Notice / eviction leases still owe rent until move-out; "current resident" style labels are active.
    if re.match(r"(on )?notice|evict", s) or re.search(r"\b(current|active|occupied)\b", s):
        return "active"
    if re.search(r"\b(past|former|moved out|expired|terminated)\b", s):
        return "expired"
    if re.search(r"\b(cancell?ed|void)\b", s):
        return "cancelled"
    if re.search(r"\b(future|pending|applicant|approved)\b", s):
        return "future"
    return "unknown"


def ticket_status(v) -> str | None:
    return _vocab(v, TICKET_STATUS)


def priority(v) -> str | None:
    return _vocab(v, PRIORITY)


def summary(v) -> str | None:
    s = text(v)
    if blank(s):
        return None
    s = _fold(s).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return _WS.sub(" ", s).strip() or None


def integer(v) -> int | None:
    s = text(v)
    if blank(s):
        return None
    try:
        return int(Decimal(s.replace(",", "")))
    except (InvalidOperation, ValueError):
        return None
