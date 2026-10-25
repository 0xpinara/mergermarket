import io

import pytest
from openpyxl import Workbook

from app import config, ingest


def test_csv_basic_with_bom_and_semicolons():
    data = "\ufefflease_id;unit_id;monthly_rent\nL1;U1;2250\nL2;U2;1900\n".encode()
    [t] = ingest.parse_upload("leases.csv", data)
    assert t.headers == ["lease_id", "unit_id", "monthly_rent"]
    assert t.rows == [["L1", "U1", "2250"], ["L2", "U2", "1900"]]
    assert t.row_numbers == [2, 3]


def test_csv_ragged_rows_and_duplicate_headers():
    data = b"id,name,name\n1,a,b,EXTRA\n2,c\n"
    [t] = ingest.parse_upload("x.csv", data)
    assert t.headers == ["id", "name", "name (2)"]
    assert t.rows[1] == ["2", "c", ""]
    assert t.warnings


def test_xlsx_multiple_sheets():
    wb = Workbook()
    ws = wb.active
    ws.title = "Leases"
    ws.append(["Lease Ref", "Unit Ref", "Base Rent", "Move-In Date"])
    from datetime import datetime
    ws.append(["L-1", "U-1", 2250.0, datetime(2025, 3, 1)])
    ws2 = wb.create_sheet("Empty")
    ws2.append([])
    buf = io.BytesIO()
    wb.save(buf)
    tables = ingest.parse_upload("cedar.xlsx", buf.getvalue())
    assert len(tables) == 1
    assert tables[0].rows == [["L-1", "U-1", "2250", "2025-03-01"]]


@pytest.mark.parametrize("name,data,msg", [
    ("x.csv", b"", "empty"),
    ("x.csv", b"\x00\x01\x02binary", "binary"),
    ("x.csv", b"2250,U1,L1\n1900,U2,L2\n", "looks like data"),
    ("x.csv", b"lease_id,unit_id\n", "no data rows"),
    ("x.csv", b"\n\n\n", "no rows"),
    ("x.pdf", b"%PDF-1.4", "unsupported"),
    ("x.xls", b"\xd0\xcf\x11\xe0", ".xls"),
    ("x.xlsx", b"not a zip", "not a valid .xlsx"),
])
def test_invalid_uploads(name, data, msg):
    with pytest.raises(ingest.UploadError, match=msg):
        ingest.parse_upload(name, data)


def test_size_and_row_limits(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(ingest.UploadError, match="larger than"):
        ingest.parse_upload("big.csv", b"a,b\n" + b"1,2\n" * 100)
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(config, "MAX_ROWS_PER_FILE", 5)
    with pytest.raises(ingest.UploadError, match="row limit"):
        ingest.parse_upload("rows.csv", b"a,b\n" + b"1,2\n" * 6)


def test_sanitize_filename():
    assert ingest.sanitize_filename("../../etc/passwd") == "passwd"
    assert ingest.sanitize_filename("C:\\Users\\x\\rent roll<script>.csv") == "rent roll_script_.csv"


@pytest.mark.parametrize("header", ["monthly_rent", "Rent Amount", "base_rent", "Base Rent ($)", "rent amount usd"])
def test_rent_aliases(header):
    m = ingest.suggest_mapping("leases", ["lease_id", "unit_number", header])
    assert m["monthly_rent"]["column"] == header
    assert m["unit_id"]["column"] == "unit_number"


def test_unit_aliases():
    m = ingest.suggest_mapping("units", ["Unit Ref", "Property Ref", "apartment"])
    assert m["unit_id"]["column"] == "Unit Ref"
    assert m["unit_label"]["column"] == "apartment"
    assert m["property_id"]["column"] == "Property Ref"


def test_entity_detection_from_headers_and_filename():
    assert ingest.detect_entity("cedar_residents.csv", ["Resident ID", "Resident Name", "Email Address"])[0] == "tenants"
    assert ingest.detect_entity("export.csv", ["Work Order #", "Unit Ref", "Status", "Description"])[0] == "maintenance"
    assert ingest.detect_entity("rentroll.csv", ["lease_id", "unit", "rent", "start"])[0] == "leases"


def test_validation_reports_missing_required_and_optional_absent():
    v = ingest.validate_mapping("leases", {"lease_id": "id", "unit_id": "unit"}, ["id", "unit"], [["L1", "U1"]])
    assert v["status"] == "needs_mapping"
    assert "Monthly rent" in v["missing"]
    v = ingest.validate_mapping("tenants", {"tenant_id": "id"}, ["id"], [["T1"]])
    assert v["status"] == "ready"  # optional columns may be absent
    assert v["warnings"]


def test_validation_counts_bad_values():
    v = ingest.validate_mapping("leases", {"lease_id": "a", "unit_id": "b", "monthly_rent": "c", "start_date": "d"},
                                ["a", "b", "c", "d"], [["L1", "U1", "abc", "soon"], ["L1", "U2", "100", "2025-01-01"]])
    assert v["stats"]["bad_money"] == 1
    assert v["stats"]["bad_dates"] == 1
    assert v["stats"]["duplicate_ids"] == 1
