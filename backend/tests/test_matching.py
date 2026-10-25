"""Matching and money-semantics behaviour on small hand-built fixtures."""
from app.engine import canonical, matching, metrics, rules
from app.ingest import suggest_mapping

LABELS = {"old": "the legacy export", "new": "the consolidated import", "old_short": "legacy",
          "new_short": "consolidated", "old_system": "legacy", "new_system": "consolidated"}


def snap(side, entity, rows, file=None):
    headers = list(rows[0].keys())
    mapping = {k: v["column"] for k, v in suggest_mapping(entity, headers).items()}
    return canonical.SnapshotInput(f"{side}-{entity}", side, entity, file or f"{side}_{entity}.csv", headers,
                                   [(i + 2, r) for i, r in enumerate(rows)], mapping)


def run(before: dict, after: dict, as_of="2026-09-30"):
    inputs = [snap("before", e, rows) for e, rows in before.items()] + \
             [snap("after", e, rows) for e, rows in after.items()]
    recs, dq, _ = canonical.build(inputs, as_of)
    S = matching.Side("before", {e: recs.get(("before", e), []) for e in before})
    T = matching.Side("after", {e: recs.get(("after", e), []) for e in after})
    M = {"properties": matching.match_properties(S, T, {})}
    M["units"] = matching.match_units(S, T, M["properties"], {})
    M["tenants"] = matching.match_tenants(S, T, {})
    M["leases"] = matching.match_leases(S, T, M["units"], M["tenants"], {})
    M["maintenance"] = matching.match_tickets(S, T, M["properties"], M["units"], {})
    ctx = rules.run_all(S, T, M, dq, LABELS, as_of)
    return S, T, M, ctx.findings


PROPS_B = [{"property_id": "P1", "property_name": "Elm Court", "street_address": "10 Elm Street"}]
PROPS_A = [{"Property Ref": "X9", "Property Name": "Elm Court", "Address": "10 Elm St."}]


def units(prefix, labels):
    return [{"unit_id": f"{prefix}{l}", "property_id": "P1" if prefix == "U" else "X9", "unit_number": l}
            for l in labels]


def test_changed_ids_match_by_business_keys():
    before = {"properties": PROPS_B, "units": units("U", ["1A"]),
              "tenants": [{"tenant_id": "T1", "full_name": "Ana Ruiz", "email": "ana@example.com"}],
              "leases": [{"lease_id": "L1", "tenant_id": "T1", "unit_id": "U1A", "start_date": "2026-01-01",
                          "status": "Active", "monthly_rent": "1500"}]}
    after = {"properties": PROPS_A, "units": [{"unit_id": "Z1", "property_id": "X9", "unit_number": "Apt 1-A"}],
             "tenants": [{"tenant_id": "R7", "full_name": "Ana Ruiz", "email": "ANA@example.com"}],
             "leases": [{"lease_id": "Q5", "tenant_id": "R7", "unit_id": "Z1", "start_date": "01/01/2026",
                         "status": "Current", "monthly_rent": "$1,500.00"}]}
    _, _, M, findings = run(before, after)
    assert M["leases"].matches["L1"].t == "Q5" and M["leases"].matches["L1"].status == "confirmed"
    assert findings == []


def test_exact_shared_ids_used_when_id_space_overlaps():
    before = {"tenants": [{"tenant_id": "T1", "full_name": "Ana Ruiz"}, {"tenant_id": "T2", "full_name": "Bo Li"}]}
    after = {"tenants": [{"tenant_id": "T1", "full_name": "Ana Ruiz"}, {"tenant_id": "T2", "full_name": "Bo Li"}]}
    _, _, M, _ = run(before, after)
    assert M["tenants"].matches["T1"].t == "T1"
    assert any("Same tenant ID" in r[0] for r in M["tenants"].matches["T1"].reasons)


def test_same_name_alone_never_collapses_two_people():
    before = {"tenants": [{"tenant_id": "T1", "full_name": "Alex Kim", "email": "alex@example.com",
                           "phone": "415-555-0101"}]}
    after = {"tenants": [{"tenant_id": "A", "full_name": "Alex Kim", "email": "other@example.com",
                          "phone": "415-555-0188"},
                         {"tenant_id": "B", "full_name": "Alex Kim", "email": "third@example.com",
                          "phone": "415-555-0177"}]}
    _, _, M, _ = run(before, after)
    assert "T1" not in M["tenants"].matches
    assert "T1" not in M["tenants"].review  # name-only evidence is below the review threshold


def test_near_tie_goes_to_review_not_auto_accept():
    before = {"properties": PROPS_B, "units": units("U", ["1A", "1B"]),
              "tenants": [{"tenant_id": "T1", "full_name": "Ana Ruiz", "phone": "415-555-0101"}],
              "leases": [{"lease_id": "L1", "tenant_id": "T1", "unit_id": "U1A", "start_date": "2026-01-01",
                          "status": "Active", "monthly_rent": "1500"}]}
    after = {"properties": PROPS_A,
             "units": [{"unit_id": "Z1A", "property_id": "X9", "unit_number": "1A"},
                       {"unit_id": "Z1B", "property_id": "X9", "unit_number": "1B"}],
             "tenants": [{"tenant_id": "R1", "full_name": "Ana Ruiz", "phone": "415.555.0101"},
                         {"tenant_id": "R2", "full_name": "Ana Ruiz", "email": "x@example.com"}],
             "leases": [{"lease_id": "Q1", "tenant_id": "R1", "unit_id": "Z1B", "start_date": "2026-01-01",
                         "status": "Current", "monthly_rent": "1500"},
                        {"lease_id": "Q2", "tenant_id": "R2", "unit_id": "Z1A", "start_date": "2026-03-01",
                         "status": "Current", "monthly_rent": "1600"}]}
    _, _, M, findings = run(before, after)
    assert "L1" not in M["leases"].matches
    assert len(M["leases"].review["L1"]) == 2
    assert [f["rule_id"] for f in findings if f["rule_id"].startswith("L-")] == ["L-006"]
    assert all(f["rent_risk_cents"] == 0 for f in findings)


def _lease_pair(after_status="Current", after_rent="1500"):
    before = {"properties": PROPS_B, "units": units("U", ["1A"]),
              "tenants": [{"tenant_id": "T1", "full_name": "Ana Ruiz", "email": "ana@example.com"}],
              "leases": [{"lease_id": "L1", "tenant_id": "T1", "unit_id": "U1A", "start_date": "2026-01-01",
                          "end_date": "2026-12-31", "status": "Active", "monthly_rent": "1500"}],
              "deposits": [{"deposit_id": "D1", "lease_id": "L1", "deposit_balance": "1500"}]}
    after = {"properties": PROPS_A, "units": [{"unit_id": "Z1", "property_id": "X9", "unit_number": "1A"}],
             "tenants": [{"tenant_id": "R7", "full_name": "Ana Ruiz", "email": "ana@example.com"}],
             "leases": [{"lease_id": "Q5", "tenant_id": "R7", "unit_id": "Z1", "start_date": "2026-01-01",
                         "end_date": "2026-12-31", "status": after_status, "monthly_rent": after_rent}],
             "deposits": [{"deposit_id": "E1", "lease_id": "Q5", "deposit_balance": "0"}]}
    return before, after


def _as_rows(findings, status="open"):
    return [{**f, "id": f["anchor"], "status": status, "active": 1} for f in findings]


def test_two_issues_on_one_lease_count_once():
    before, after = _lease_pair(after_status="Expired", after_rent="1200")
    _, _, _, findings = run(before, after)
    rent = [f for f in findings if f["rent_risk_cents"]]
    assert sorted(f["rule_id"] for f in rent) == ["L-002", "L-004"]
    total, lines, counted = metrics.attribute_rent(_as_rows(findings))
    assert total == 150000  # full rent once, not 150000 + 30000
    assert len(lines) == 1 and lines[0]["also"][0]["cents"] == 30000


def test_deposit_discrepancy_never_enters_rent():
    before, after = _lease_pair()
    _, _, _, findings = run(before, after)
    [d] = [f for f in findings if f["rule_id"] == "D-002"]
    assert d["deposit_cents"] == 150000 and d["rent_risk_cents"] == 0
    total, _, _ = metrics.attribute_rent(_as_rows(findings))
    assert total == 0


def test_expired_lease_missing_is_informational():
    before, after = _lease_pair()
    before["tenants"].append({"tenant_id": "T0", "full_name": "Old Tenant", "email": "old@example.com"})
    before["leases"].append({"lease_id": "L0", "tenant_id": "T0", "unit_id": "U1A", "start_date": "2024-01-01",
                             "end_date": "2024-12-31", "status": "Expired", "monthly_rent": "1400"})
    _, _, _, findings = run(before, after)
    [f] = [f for f in findings if f["rule_id"] == "L-012"]
    assert f["severity"] == "low" and f["rent_risk_cents"] == 0


def test_completed_tickets_informational_open_ticket_missing_flagged():
    base_b, base_a = _lease_pair()
    base_b["maintenance"] = [
        {"ticket_id": "W1", "unit_id": "U1A", "summary": "Leak under sink", "priority": "High", "status": "Open",
         "opened_on": "2026-09-01"},
        {"ticket_id": "W2", "unit_id": "U1A", "summary": "Paint touch-up", "priority": "Low", "status": "Completed",
         "opened_on": "2026-06-01"},
        {"ticket_id": "W3", "unit_id": "U1A", "summary": "Door sticks", "priority": "Normal", "status": "Open",
         "opened_on": "2026-09-05"}]
    base_a["maintenance"] = [{"ticket_id": "N3", "unit_id": "Z1", "summary": "door sticks.", "priority": "Medium",
                              "status": "New", "opened_on": "09/05/2026"}]
    _, _, M, findings = run(base_b, base_a)
    rules_ = sorted(f["rule_id"] for f in findings if f["rule_id"].startswith("M-"))
    assert rules_ == ["M-001", "M-004"]
    assert M["maintenance"].matches["W3"].t == "N3"


def test_orphan_reference_and_unexpected_lease():
    before, after = _lease_pair()
    after["units"].append({"unit_id": "Z2", "property_id": "X9", "unit_number": "2B"})
    after["tenants"].append({"tenant_id": "R8", "full_name": "Kai Moss", "email": "kai@example.com"})
    after["leases"][0]["unit_id"] = "NOPE"
    after["leases"].append({"lease_id": "Q9", "tenant_id": "R8", "unit_id": "Z2", "start_date": "2026-11-01",
                            "status": "Current", "monthly_rent": "1700"})
    after["leases"].append({"lease_id": "Q10", "tenant_id": "GHOST", "unit_id": "MISSING", "start_date": "2026-02-01",
                            "status": "Current", "monthly_rent": "1100"})
    _, _, M, findings = run(before, after)
    assert M["leases"].matches["L1"].t == "Q5"
    by = {f["rule_id"]: f for f in findings if f["rule_id"].startswith("L-")}
    assert "NOPE" in by["L-009"]["explanation"]
    unexpected = sorted((f["severity"], f["target"]["id"]) for f in findings if f["rule_id"] == "L-011")
    assert unexpected == [("low", "Q9"), ("medium", "Q10")]
    q10 = next(f for f in findings if f["rule_id"] == "L-011" and f["target"]["id"] == "Q10")
    assert "MISSING" in q10["explanation"] and "GHOST" in q10["explanation"]


def test_renewal_history_is_not_a_review_item():
    before, after = _lease_pair()
    before["leases"].append({"lease_id": "L0", "tenant_id": "T1", "unit_id": "U1A", "start_date": "2025-01-01",
                             "end_date": "2025-12-31", "status": "Expired", "monthly_rent": "1450"})
    _, _, _, findings = run(before, after)
    assert [f["rule_id"] for f in findings if f["rule_id"].startswith("L-")] == ["L-012"]


def test_leases_only_rent_roll_comparison():
    before = {"leases": [{"lease_id": "A1", "tenant": "Ana Ruiz", "unit": "Apt 1A", "property": "Elm Court",
                          "rent": "1500", "status": "Active"},
                         {"lease_id": "A2", "tenant": "Bo Li", "unit": "1B", "property": "Elm Court",
                          "rent": "1600", "status": "Active"}]}
    after = {"leases": [{"lease_id": "Z7", "tenant": "Ana Ruiz", "unit": "#1-A", "property": "Elm Court",
                         "rent": "$1,300.00", "status": "Current"},
                        {"lease_id": "Z8", "tenant": "Bo Li", "unit": "1B", "property": "Elm Court",
                         "rent": "1600", "status": "Current"}]}
    _, _, M, findings = run(before, after)
    assert M["leases"].matches["A1"].t == "Z7" and M["leases"].matches["A2"].t == "Z8"
    assert [(f["rule_id"], f["rent_risk_cents"]) for f in findings] == [("L-002", 20000)]


def test_unreadable_money_is_a_data_quality_finding():
    before, after = _lease_pair()
    after["leases"][0]["monthly_rent"] = "call office"
    _, _, _, findings = run(before, after)
    rules_ = {f["rule_id"] for f in findings}
    assert "Q-002" in rules_ and "L-002" in rules_
    l002 = next(f for f in findings if f["rule_id"] == "L-002")
    assert l002["severity"] == "critical" and l002["rent_risk_cents"] == 150000
