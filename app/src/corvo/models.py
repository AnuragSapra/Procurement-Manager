from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class Item:
    code: str
    description: str
    uom: str
    category: str
    item_type: str
    standard_price: Decimal


@dataclass
class Supplier:
    id: str
    name: str
    city: str
    categories: tuple[str, ...]
    status: str
    on_time: Decimal
    quality: Decimal


@dataclass
class Purchase:
    number: str
    number_i: int
    po_date: date
    supplier_id: str
    item_code: str
    quantity: Decimal
    uom: str
    unit_price: Decimal
    plant: str


@dataclass
class Contract:
    id: str
    supplier_id: str
    supplier_name: str
    start: date
    end: date
    cap: Decimal
    consumed: Decimal
    rates: dict[str, Decimal]
    source: str


@dataclass
class Catalog:
    items: dict[str, Item]
    suppliers: dict[str, Supplier]
    plants: set[str]
    purchases: list[Purchase]
    contracts: list[Contract]


@dataclass
class PRLine:
    pr_no: str
    pr_line: int
    created_at: datetime
    requester: str
    department: str
    plant: str
    item_code: str
    description: str
    quantity: Decimal
    uom: str
    estimate: Decimal | None
    need_by: date
    urgency: str
    resolved_item: str | None = None
    match_note: str = ""


@dataclass
class Decision:
    pr_no: str
    pr_line: int
    resolved_item_code: str
    group_id: str
    po_id: str
    decision: str
    route: str
    supplier_id: str
    unit_price: str
    group_value_inr: str
    suggested_supplier_ids: str
    policy_version: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "pr_no": self.pr_no,
            "pr_line": str(self.pr_line),
            "resolved_item_code": self.resolved_item_code,
            "group_id": self.group_id,
            "po_id": self.po_id,
            "decision": self.decision,
            "route": self.route,
            "supplier_id": self.supplier_id,
            "unit_price": self.unit_price,
            "group_value_inr": self.group_value_inr,
            "suggested_supplier_ids": self.suggested_supplier_ids,
            "policy_version": self.policy_version,
            "reason": self.reason,
        }


@dataclass
class Requirement:
    lines: list[PRLine] = field(default_factory=list)
    group_id: str = ""

    @property
    def first(self) -> PRLine:
        return self.lines[0]
