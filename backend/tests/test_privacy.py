"""Privacy shield: tenant PII becomes stable per-workspace tokens; evidence stays quotable."""
from app import db, privacy, services


def _shield(full=(), parts=(), emails=(), phones=(), vocab=(), secret=b"k" * 32):
    return privacy.Shield(secret, set(full), set(parts), set(emails), set(phones), set(vocab))


def test_tokens_replace_people_but_keep_evidence_quotable():
    s = _shield(full={"Rhea Achebe"}, parts={"Rhea", "Achebe"}, emails={"Rhea.Achebe@example.com"},
                phones={"(650) 555-0104"})
    text = ("Rhea Achebe's lease BV-L-1001 (bayview_leases.csv row 2, unit BV-U-0001) ended 2026-09-30 / 09/30/2026; "
            "rent $2,400.00 → $1,950.00 (−$450.00, +$450.00, 38711500 cents). Contact rhea.achebe@example.com, "
            "650.555.0104 or +1 650-555-0104. Lease CPM-L-50001 for Achebe, Rhea.")
    out = s.text(text)
    for kept in ("BV-L-1001", "row 2", "BV-U-0001", "2026-09-30", "09/30/2026", "$2,400.00", "−$450.00", "+$450.00",
                 "38711500", "CPM-L-50001", "bayview_leases.csv"):
        assert kept in out, kept
    for gone in ("Rhea", "Achebe", "example.com", "555-0104", "555.0104"):
        assert gone not in out, gone
    person = privacy.token(b"k" * 32, "person", "rhea achebe")
    phone = privacy.token(b"k" * 32, "phone", "6505550104")
    assert out.count(person) == 2 and out.count(phone) == 2   # every phone format → one stable token
    assert privacy.TOKEN_RE.search(out)
    # whole cell values and nested structures
    assert s.apply({"first_name": "Rhea", "rows": [{"phone": "650-555-0104"}], "n": 3}) == {
        "first_name": privacy.token(b"k" * 32, "person", "rhea"), "rows": [{"phone": phone}], "n": 3}


def test_catch_all_shapes_without_eating_ids_dates_or_money():
    s = _shield()
    out = s.text("Call (415) 555-0191 or +44 20 7946 0958, mail x.y@z.co — IDs BV-WO-3001, CU-000123, 2025-01-01, "
                 "$1,234,567.89, 1450 Marigold Avenue, F-1A2B3C4D5E")
    assert "555-0191" not in out and "7946" not in out and "x.y@z.co" not in out
    for kept in ("BV-WO-3001", "CU-000123", "2025-01-01", "$1,234,567.89", "1450 Marigold Avenue", "F-1A2B3C4D5E"):
        assert kept in out, kept


def test_lone_name_parts_spare_ordinary_words():
    s = _shield(full={"Mae Juniper"}, parts={"Mae", "Juniper"}, vocab={"juniper", "terrace"})
    out = s.text("Mae Juniper · Unit 4B · Juniper Terrace; Mae called.")
    assert "Juniper Terrace" in out and "Mae Juniper" not in out and "Mae called" not in out


def test_tokens_are_stable_per_workspace_and_differ_across(tmpdb):
    with db.connect() as conn:
        a = services.load_sample(conn, "ws_priv_aaaa01")
        b = services.load_sample(conn, "ws_priv_bbbb02")
        t1 = privacy.shield(conn, "ws_priv_aaaa01", a, "Dana Whitfield")
        t2 = privacy.shield(conn, "ws_priv_aaaa01", a, ["Dana Whitfield", "dana whitfield"])
        t3 = privacy.shield(conn, "ws_priv_bbbb02", b, "Dana Whitfield")
        secrets = [r[0] for r in conn.execute("SELECT secret FROM pii_secrets ORDER BY workspace")]
    assert t1.startswith("PERSON_") and t2 == [t1, t1] and t3.startswith("PERSON_") and t3 != t1
    assert len(secrets) == 2 and secrets[0] != secrets[1] and all(len(s) == 64 for s in secrets)
    assert secrets[0] not in t1
