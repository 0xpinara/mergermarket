"""Canonical property-management entities, their fields, and the column aliases
used to suggest a mapping for an uploaded export."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"  # id | text | name | money | date | email | phone | lease_status | ticket_status | priority | address | unit | int
    required: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntitySpec:
    key: str
    label: str
    singular: str
    id_field: str
    keywords: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    one_of: tuple[tuple[str, ...], ...] = ()
    description: str = ""

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)


LEGACY_REF = FieldSpec(
    "legacy_ref", "Legacy reference", "id", False,
    ("legacy ref", "legacy id", "legacy reference", "external id", "external ref", "source id", "old id",
     "prior system id", "previous id", "migrated from", "legacy key"),
)

ENTITIES: dict[str, EntitySpec] = {
    "properties": EntitySpec(
        "properties", "Properties", "property", "property_id",
        ("propert", "building", "portfolio", "site", "communit"),
        (
            FieldSpec("property_id", "Property ID", "id", True,
                      ("property id", "property ref", "property reference", "property code", "property number",
                       "building id", "building ref", "prop id", "site id", "id")),
            FieldSpec("name", "Property name", "name", False,
                      ("property name", "name", "building name", "property", "building", "community", "site name")),
            FieldSpec("address", "Street address", "address", False,
                      ("address", "street address", "street", "address 1", "address line 1", "property address",
                       "address1", "street line")),
            FieldSpec("city", "City", "text", False, ("city", "town", "municipality")),
            FieldSpec("state", "State", "text", False, ("state", "province", "region", "st")),
            FieldSpec("postal_code", "Postal code", "text", False, ("zip", "zip code", "postal code", "postcode")),
            LEGACY_REF,
        ),
        one_of=(("name", "address"),),
        description="Buildings or communities in the portfolio.",
    ),
    "units": EntitySpec(
        "units", "Units", "unit", "unit_id",
        ("unit", "apartment", "door", "space"),
        (
            FieldSpec("unit_id", "Unit ID", "id", True,
                      ("unit id", "unit ref", "unit reference", "unit code", "apartment id", "space id", "door id", "id")),
            FieldSpec("property_id", "Property ID", "id", True,
                      ("property id", "property ref", "property reference", "property code", "building id",
                       "building ref", "property", "building", "prop id")),
            FieldSpec("unit_label", "Unit number / label", "unit", True,
                      ("unit", "unit number", "unit no", "unit label", "unit name", "apartment", "apt", "apt number",
                       "apartment number", "suite", "door", "unit address", "unit #")),
            FieldSpec("bedrooms", "Bedrooms", "int", False, ("beds", "bedrooms", "br", "bed", "bed count")),
            LEGACY_REF,
        ),
        description="Leasable units, linked to a property.",
    ),
    "tenants": EntitySpec(
        "tenants", "Tenants", "tenant", "tenant_id",
        ("tenant", "resident", "renter", "occupant", "people", "contact", "customer"),
        (
            FieldSpec("tenant_id", "Tenant ID", "id", True,
                      ("tenant id", "tenant ref", "resident id", "resident ref", "renter id", "occupant id",
                       "contact id", "customer id", "person id", "id")),
            FieldSpec("full_name", "Full name", "name", False,
                      ("name", "full name", "tenant name", "resident name", "tenant", "resident", "occupant",
                       "primary tenant", "renter name", "contact name")),
            FieldSpec("first_name", "First name", "name", False,
                      ("first name", "given name", "first", "fname", "forename")),
            FieldSpec("last_name", "Last name", "name", False,
                      ("last name", "surname", "family name", "last", "lname")),
            FieldSpec("email", "Email", "email", False,
                      ("email", "email address", "e mail", "tenant email", "resident email", "contact email",
                       "primary email", "mail")),
            FieldSpec("phone", "Phone", "phone", False,
                      ("phone", "phone number", "mobile", "cell", "telephone", "tel", "contact phone", "primary phone",
                       "mobile phone", "cell phone")),
            LEGACY_REF,
        ),
        description="People on leases. Only an ID is required; email/phone make matching stronger.",
    ),
    "leases": EntitySpec(
        "leases", "Leases", "lease", "lease_id",
        ("lease", "rent roll", "rentroll", "rent_roll", "agreement", "tenancy", "tenancies"),
        (
            FieldSpec("lease_id", "Lease ID", "id", True,
                      ("lease id", "lease ref", "lease reference", "lease number", "lease no", "agreement id",
                       "contract id", "tenancy id", "id")),
            FieldSpec("tenant_id", "Tenant ID", "id", False,
                      ("tenant id", "tenant ref", "resident id", "resident ref", "primary tenant id", "renter id",
                       "occupant id", "tenant", "resident")),
            FieldSpec("unit_id", "Unit reference", "id", True,
                      ("unit id", "unit ref", "unit reference", "unit", "apartment", "apartment id", "unit number",
                       "unit code", "apt", "unit no", "space id")),
            FieldSpec("property_id", "Property ID", "id", False,
                      ("property id", "property ref", "property", "building id", "building")),
            FieldSpec("start_date", "Start date", "date", False,
                      ("start date", "lease start", "move in", "move in date", "commencement date", "lease from",
                       "start", "begin date", "from", "effective date", "lease start date")),
            FieldSpec("end_date", "End date", "date", False,
                      ("end date", "lease end", "move out", "move out date", "expiration", "expiration date",
                       "expiry", "lease to", "end", "to", "lease expiration", "lease end date")),
            FieldSpec("status", "Lease status", "lease_status", False,
                      ("status", "lease status", "state", "occupancy status", "lease state")),
            FieldSpec("monthly_rent", "Monthly rent", "money", True,
                      ("monthly rent", "rent", "rent amount", "base rent", "current rent", "monthly amount",
                       "scheduled rent", "contract rent", "lease rent", "rent monthly", "monthly charge",
                       "rent per month")),
            LEGACY_REF,
        ),
        description="Lease agreements with rent and term.",
    ),
    "deposits": EntitySpec(
        "deposits", "Security deposits", "deposit", "deposit_id",
        ("deposit", "security", "escrow"),
        (
            FieldSpec("deposit_id", "Deposit ID", "id", False,
                      ("deposit id", "deposit ref", "deposit reference", "deposit number", "id")),
            FieldSpec("lease_id", "Lease ID", "id", False, ("lease id", "lease ref", "lease", "lease reference")),
            FieldSpec("tenant_id", "Tenant ID", "id", False,
                      ("tenant id", "resident id", "tenant ref", "resident ref", "tenant")),
            FieldSpec("balance", "Held balance", "money", True,
                      ("balance", "deposit", "deposit balance", "deposit held", "held balance", "security deposit",
                       "deposit amount", "amount held", "held", "sd balance", "amount", "deposit on hand")),
        ),
        one_of=(("lease_id", "tenant_id"),),
        description="Security-deposit balances held — a tenant liability, tracked separately from rent.",
    ),
    "maintenance": EntitySpec(
        "maintenance", "Maintenance requests", "work order", "ticket_id",
        ("maint", "work order", "workorder", "work_order", "ticket", "service", "repair", "request"),
        (
            FieldSpec("ticket_id", "Ticket ID", "id", True,
                      ("ticket id", "work order", "work order id", "work order number", "wo number", "wo id", "wo",
                       "request id", "ticket", "ticket number", "service request id", "id")),
            FieldSpec("property_id", "Property ID", "id", False,
                      ("property id", "property ref", "property", "building id", "building")),
            FieldSpec("unit_id", "Unit reference", "id", False,
                      ("unit id", "unit ref", "unit", "apartment", "unit number", "apt")),
            FieldSpec("summary", "Issue summary", "text", False,
                      ("summary", "description", "issue", "issue summary", "title", "subject", "problem", "details",
                       "request", "issue description")),
            FieldSpec("priority", "Priority", "priority", False, ("priority", "urgency", "severity", "level")),
            FieldSpec("status", "Status", "ticket_status", True,
                      ("status", "state", "ticket status", "work order status", "wo status")),
            FieldSpec("opened_date", "Opened date", "date", False,
                      ("opened date", "opened", "opened on", "created", "created date", "created on", "date opened",
                       "reported", "reported date", "submitted", "request date", "date")),
            LEGACY_REF,
        ),
        one_of=(("property_id", "unit_id"),),
        description="Work orders. Open and in-progress requests are expected to carry over.",
    ),
}

ENTITY_ORDER = ["properties", "units", "tenants", "leases", "deposits", "maintenance"]


def schema_payload() -> list[dict]:
    out = []
    for key in ENTITY_ORDER:
        e = ENTITIES[key]
        out.append({
            "key": e.key, "label": e.label, "singular": e.singular, "description": e.description,
            "id_field": e.id_field, "one_of": [list(g) for g in e.one_of],
            "fields": [{k: v for k, v in asdict(f).items() if k != "aliases"} for f in e.fields],
        })
    return out
