from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal

POLICY_V5: dict = {
    "version": "5.0",
    "effective_from": "2026-04-01",
    "parameters": {
        "club_window_days": 14,
        "need_by_window_days": 7,
        "history_days": 365,
        "preferred_lookback_days": 548,
        "recency_recent_days": 90,
        "recency_year_days": 365,
        "po_consolidation_hours": 24,
        "emergency_max": 500000,
        "spot_max": 200000,
        "rfq_max": 1000000,
        "suggest_rfq": 3,
        "suggest_rfp": 5,
        "suggest_capex": 5,
        "rank_po_weight": 100,
        "rank_recency_weight": 500,
        "rank_contract_weight": 200,
        "rank_ontime_weight": 0.1,
        "rank_quality_weight": 1,
    },
    "rules": [
        {
            "id": "1.2",
            "when": {"all": [{"fact": "item_is_new", "op": "eq", "value": True}]},
            "outcome": {
                "decision": "SOURCING_REQUEST",
                "route": "NEW_ITEM_SOURCING",
                "supplier": "none",
                "price": "estimate",
                "suggest": 0,
            },
        },
        {
            "id": "5.1",
            "when": {"all": [{"fact": "item_type", "op": "eq", "value": "Capex"}]},
            "outcome": {
                "decision": "SOURCING_REQUEST",
                "route": "CAPEX_SOURCING",
                "supplier": "none",
                "price": "none",
                "suggest_from": "suggest_capex",
            },
        },
        {
            "id": "5.2",
            "when": {"all": [{"fact": "contract_usable", "op": "eq", "value": True}]},
            "outcome": {
                "decision": "PO",
                "route": "CONTRACT_PO",
                "supplier": "contract",
                "price": "contract_rate",
                "suggest": 0,
            },
        },
        {
            "id": "5.3",
            "when": {
                "all": [
                    {"fact": "urgency", "op": "eq", "value": "Emergency"},
                    {"fact": "value", "op": "lte", "value_from": "emergency_max"},
                    {"fact": "bought_recently", "op": "eq", "value": True},
                    {"fact": "has_preferred", "op": "eq", "value": True},
                ]
            },
            "outcome": {
                "decision": "PO",
                "route": "EMERGENCY_PO",
                "supplier": "preferred_1",
                "price": "reference_price",
                "suggest": 0,
            },
        },
        {
            "id": "5.4",
            "when": {
                "all": [
                    {"fact": "value", "op": "lt", "value_from": "spot_max"},
                    {"fact": "bought_recently", "op": "eq", "value": True},
                    {"fact": "has_preferred", "op": "eq", "value": True},
                ]
            },
            "outcome": {
                "decision": "PO",
                "route": "SPOT_PO",
                "supplier": "preferred_1",
                "price": "reference_price",
                "suggest": 0,
            },
        },
        {
            "id": "5.5",
            "when": {"all": [{"fact": "value", "op": "lt", "value_from": "rfq_max"}]},
            "outcome": {
                "decision": "SOURCING_REQUEST",
                "route": "RFQ",
                "supplier": "none",
                "price": "none",
                "suggest_from": "suggest_rfq",
            },
        },
        {
            "id": "5.6",
            "when": {"all": [{"fact": "value", "op": "gte", "value_from": "rfq_max"}]},
            "outcome": {
                "decision": "SOURCING_REQUEST",
                "route": "RFP",
                "supplier": "none",
                "price": "none",
                "suggest_from": "suggest_rfp",
            },
        },
    ],
}

PARAMETER_LABELS = {
    "spot_max": "Spot PO limit",
    "emergency_max": "Emergency PO limit",
    "rfq_max": "RFQ ceiling (RFP starts at this amount)",
    "club_window_days": "Clubbing window (days)",
    "need_by_window_days": "Need-by window (days)",
    "history_days": "Purchase-history window (days)",
    "po_consolidation_hours": "PO consolidation window (hours)",
}

FACT_LABELS = {
    "plant": "plant",
    "category": "category",
    "item_type": "item type",
    "department": "department",
    "urgency": "urgency",
    "value": "value",
}


def policy_for(policies: list[dict], on: date) -> dict:
    eligible = [
        policy
        for policy in policies
        if policy.get("status", "approved") == "approved"
        and date.fromisoformat(policy["effective_from"]) <= on
    ]
    if not eligible:
        raise ValueError(f"No approved policy is effective on {on.isoformat()}")
    return max(
        eligible,
        key=lambda policy: (
            date.fromisoformat(policy["effective_from"]),
            _version_key(policy.get("version", "0")),
        ),
    )


def _version_key(version: str) -> tuple[int, ...]:
    numbers = []
    for part in version.split("."):
        numbers.append(int(part) if part.isdigit() else 0)
    return tuple(numbers)


def _param(policy: dict, clause: dict):
    if "value_from" in clause:
        return policy["parameters"][clause["value_from"]]
    return clause.get("value")


def _holds(clause: dict, facts: dict, policy: dict) -> bool:
    if "all" in clause:
        return all(_holds(part, facts, policy) for part in clause["all"])
    if "any" in clause:
        return any(_holds(part, facts, policy) for part in clause["any"])
    left = facts[clause["fact"]]
    right = _param(policy, clause)
    op = clause["op"]
    if isinstance(left, Decimal) or isinstance(right, (int, float)) and not isinstance(right, bool):
        left_n = Decimal(str(left))
        right_n = Decimal(str(right))
        return {
            "eq": left_n == right_n,
            "lt": left_n < right_n,
            "lte": left_n <= right_n,
            "gt": left_n > right_n,
            "gte": left_n >= right_n,
            "neq": left_n != right_n,
        }[op]
    return {
        "eq": left == right,
        "neq": left != right,
    }[op]


def first_rule(policy: dict, facts: dict) -> dict | None:
    for rule in policy["rules"]:
        if _holds(rule["when"], facts, policy):
            return rule
    return None


def suggest_count(policy: dict, outcome: dict) -> int:
    if "suggest_from" in outcome:
        return int(policy["parameters"][outcome["suggest_from"]])
    return int(outcome.get("suggest", 0))


def next_version(version: str) -> str:
    major, minor = version.split(".")
    return f"{major}.{int(minor) + 1}"


def explain_policy(policy: dict) -> list[str]:
    params = policy["parameters"]
    lines = [
        f"Version {policy['version']}, effective {policy['effective_from']}.",
        f"Club lines within {params['club_window_days']} days, need-by within {params['need_by_window_days']} days.",
        f"Emergency PO up to ₹{int(params['emergency_max']):,}. Spot PO below ₹{int(params['spot_max']):,}. "
        f"RFQ below ₹{int(params['rfq_max']):,}, otherwise RFP.",
    ]
    for rule in policy["rules"]:
        lines.append(f"Rule {rule['id']}: {_when_text(rule['when'])} → {rule['outcome']['route']}.")
    return lines


def _when_text(clause: dict) -> str:
    if "all" in clause:
        return " and ".join(_when_text(part) for part in clause["all"])
    if "any" in clause:
        return " or ".join(_when_text(part) for part in clause["any"])
    value = clause.get("value_from") or clause.get("value")
    return f"{clause['fact']} {clause['op']} {value}"


def diff_policies(old: dict, new: dict) -> list[str]:
    notes: list[str] = [f"New version {new['version']} effective {new['effective_from']}."]
    for key, label in PARAMETER_LABELS.items():
        if old["parameters"].get(key) != new["parameters"].get(key):
            notes.append(f"{label} changes from {old['parameters'].get(key)} to {new['parameters'].get(key)}.")
    old_ids = [rule["id"] for rule in old["rules"]]
    for rule in new["rules"]:
        if rule["id"] not in old_ids:
            notes.append(
                f"New rule {rule['id']} before the later rules: {_when_text(rule['when'])} → {rule['outcome']['route']}."
            )
    if len(notes) == 1:
        notes.append("No parameter or rule change was detected.")
    return notes


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _find_date(text: str) -> date | None:
    iso = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if iso:
        return date.fromisoformat(iso.group(1))
    written = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})", text
    )
    if written and written.group(2).lower() in _MONTHS:
        return date(int(written.group(3)), _MONTHS[written.group(2).lower()], int(written.group(1)))
    return None


def _money(text: str) -> int | None:
    match = re.search(r"₹?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac)?", text, re.I)
    if not match:
        return None
    amount = Decimal(match.group(1).replace(",", ""))
    if match.group(2):
        amount *= 100000
    return int(amount)


def parse_amendment(text: str, current: dict) -> tuple[dict, list[str]]:
    """Read a plain-language amendment into a new policy version.

    Also accepts a full policy JSON document.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        document = json.loads(stripped)
        document.setdefault("status", "pending")
        notes = ["Uploaded a policy document."] + diff_policies(current, document)
        return document, notes

    updated = json.loads(json.dumps(current))
    updated["version"] = next_version(current["version"])
    updated["status"] = "pending"
    notes: list[str] = []

    effective = _find_date(stripped)
    if effective:
        updated["effective_from"] = effective.isoformat()
        notes.append(f"Effective {effective.isoformat()}.")
    else:
        notes.append("No effective date was found. Add one before approval.")

    replacements = (
        (r"spot(?:\s+PO)?\s+limit(?:\s+is|\s+to|\s+of)?\s+₹?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac)?", "spot_max"),
        (r"emergency(?:\s+PO)?\s+limit(?:\s+is|\s+to|\s+of)?\s+₹?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac)?", "emergency_max"),
        (r"(?:RFQ ceiling|RFP starts at|RFP threshold)(?:\s+is|\s+to|\s+of)?\s+₹?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac)?", "rfq_max"),
        (r"club(?:bing)? window(?:\s+is|\s+to|\s+of)?\s+(\d+)\s+days", "club_window_days"),
        (r"need-by window(?:\s+is|\s+to|\s+of)?\s+(\d+)\s+days", "need_by_window_days"),
    )
    for pattern, key in replacements:
        match = re.search(pattern, stripped, re.I)
        if not match:
            continue
        if key.endswith("_days"):
            value = int(match.group(1))
        else:
            value = Decimal(match.group(1).replace(",", ""))
            if match.lastindex and match.lastindex >= 2 and match.group(2):
                value *= 100000
            value = int(value)
        old = updated["parameters"][key]
        updated["parameters"][key] = value
        notes.append(f"{PARAMETER_LABELS[key]} changes from {old} to {value}.")

    for match in re.finditer(
        r"if\s+(.+?)\s+then\s+(SPOT_PO|EMERGENCY_PO|RFQ|RFP|CAPEX_SOURCING|CONTRACT_PO|NEW_ITEM_SOURCING)",
        stripped,
        re.I,
    ):
        rule = _rule_from_clause(match.group(1), match.group(2).upper(), updated)
        updated["rules"].insert(_insert_at(updated["rules"], rule["outcome"]["route"]), rule)
        notes.append(f"New rule {rule['id']}: {_when_text(rule['when'])} → {rule['outcome']['route']}.")

    if not any(note.startswith("Spot") or note.startswith("Emergency") or note.startswith("RFQ") or note.startswith("Club") or note.startswith("Need-by") or note.startswith("New rule") or note.startswith("Uploaded") for note in notes):
        if effective:
            notes.append("The date was read, but no limit or new rule was recognised.")
    return updated, notes


def _insert_at(rules: list[dict], route: str) -> int:
    anchors = {"SPOT_PO": "5.4", "EMERGENCY_PO": "5.3", "RFQ": "5.5", "RFP": "5.6"}
    target = anchors.get(route)
    if target:
        for index, rule in enumerate(rules):
            if rule["id"] == target:
                return index
    return max(1, len(rules) - 2)


def _rule_from_clause(clause: str, route: str, policy: dict) -> dict:
    conditions: list[dict] = []
    plant = re.search(r"plant\s+(?:is\s+)?(PUN\d|CHK\d)", clause, re.I)
    if plant:
        conditions.append({"fact": "plant", "op": "eq", "value": plant.group(1).upper()})
    category = re.search(r"category\s+(?:is\s+)?[\"']?([A-Za-z][A-Za-z &]+?)[\"']?(?:\s+and|\s+with|\s+value|$)", clause, re.I)
    if category:
        conditions.append({"fact": "category", "op": "eq", "value": category.group(1).strip()})
    item_type = re.search(r"item type\s+(?:is\s+)?(Capex|Material|Service)", clause, re.I)
    if item_type:
        conditions.append({"fact": "item_type", "op": "eq", "value": item_type.group(1).capitalize()})
    value = re.search(r"value\s*(<|<=|>|>=)\s*₹?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac)?", clause, re.I)
    if value:
        amount = Decimal(value.group(2).replace(",", ""))
        if value.group(3):
            amount *= 100000
        op = {"<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}[value.group(1)]
        conditions.append({"fact": "value", "op": op, "value": int(amount)})
    if re.search(r"emergency", clause, re.I):
        conditions.append({"fact": "urgency", "op": "eq", "value": "Emergency"})
    without_history = re.search(r"without\s+(?:purchase\s+)?history", clause, re.I)
    if route in {"SPOT_PO", "EMERGENCY_PO"} and not without_history:
        conditions.append({"fact": "bought_recently", "op": "eq", "value": True})
        conditions.append({"fact": "has_preferred", "op": "eq", "value": True})
    rule_id = f"A{sum(1 for rule in policy['rules'] if str(rule['id']).startswith('A')) + 1}"
    supplier = "preferred_1" if route in {"SPOT_PO", "EMERGENCY_PO"} else "none"
    price = "reference_price" if supplier == "preferred_1" else "none"
    suggest = {"RFQ": 3, "RFP": 5, "CAPEX_SOURCING": 5}.get(route, 0)
    return {
        "id": rule_id,
        "when": {"all": conditions or [{"fact": "plant", "op": "eq", "value": ""}]},
        "outcome": {
            "decision": "PO" if route.endswith("_PO") and route != "NEW_ITEM_SOURCING" else "SOURCING_REQUEST",
            "route": route,
            "supplier": supplier,
            "price": price,
            "suggest": suggest,
        },
    }


def stamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")
