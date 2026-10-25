from datetime import date, datetime

import pytest

from app import normalize as nz


@pytest.mark.parametrize("raw,iso", [
    ("2025-03-01", "2025-03-01"), ("03/01/2025", "2025-03-01"), ("3/1/25", "2025-03-01"),
    ("Mar 1, 2025", "2025-03-01"), ("01-Mar-2025", "2025-03-01"), ("2025-03-01T00:00:00", "2025-03-01"),
    (datetime(2025, 3, 1, 9, 30), "2025-03-01"), (date(2025, 3, 1), "2025-03-01"), (45717, "2025-03-01"),
    ("", None), ("TBD", None), (None, None),
])
def test_parse_date(raw, iso):
    assert nz.parse_date(raw) == iso


@pytest.mark.parametrize("raw,cents", [
    ("$2,250.00", 225000), ("2250", 225000), ("2250.5", 225050), ("(450.00)", -45000), ("USD 1,875", 187500),
    ("1,875.005", 187501), (2050, 205000), (2050.0, 205000), ("-12.40", -1240), ("", None), (None, None),
])
def test_parse_money(raw, cents):
    assert nz.parse_money(raw) == cents


def test_parse_money_rejects_text():
    with pytest.raises(nz.MoneyError):
        nz.parse_money("call office")


def test_contact_normalizers():
    assert nz.email(" Priya.Raman@Example.COM ") == "priya.raman@example.com"
    assert nz.email("not-an-email") is None
    assert nz.phone("(510) 555-0123") == nz.phone("510.555.0123") == nz.phone("+1 510-555-0123") == "5105550123"
    assert nz.phone("12") is None
    assert nz.name("  Grünewald,  Ana ") == "grunewald ana"


def test_address_and_unit():
    assert nz.address("1450 Marigold Avenue") == nz.address("1450 Marigold Ave.") == "1450 marigold ave"
    assert nz.address("88 Juniper Terrace") == nz.address("88 juniper ter")
    for raw in ("Apt 4B", "#4-B", "4b", "Unit 04B", "APT. 4 B"):
        assert nz.unit_label(raw) == "4B", raw
    assert nz.floor_of("4B") == 4
    assert nz.floor_of("1203") == 12
    assert nz.floor_of("PH") is None


def test_vocabularies():
    assert nz.lease_status("Current") == nz.lease_status("ACTIVE") == "active"
    assert nz.lease_status("Past") == "expired"
    assert nz.lease_status("weird") == "unknown"
    assert nz.ticket_status("New") == "open"
    assert nz.ticket_status("In Progress") == "in_progress"
    assert nz.ticket_status("Closed") == nz.ticket_status("Completed") == "closed"
    assert nz.priority("Emergency") == nz.priority("Urgent") == "urgent"
    assert nz.priority("Normal") == nz.priority("Medium") == "medium"


def test_cents_to_str():
    assert nz.cents_to_str(45000) == "$450.00"
    assert nz.cents_to_str(-45000) == "−$450.00"
    assert nz.cents_to_str(14500, signed=True) == "+$145.00"
