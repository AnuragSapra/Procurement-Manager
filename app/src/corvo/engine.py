from __future__ import annotations

import csv
import io
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from corvo.match import Matcher
from corvo.models import Catalog, Decision, PRLine, Requirement
from corvo.policy import first_rule, policy_for, suggest_count
from corvo.ranking import latest_active_purchase, rank_item, suggest

EXPORT_COLUMNS = [
    "pr_no",
    "pr_line",
    "resolved_item_code",
    "group_id",
    "po_id",
    "decision",
    "route",
    "supplier_id",
    "unit_price",
    "group_value_inr",
    "suggested_supplier_ids",
    "policy_version",
    "reason",
]


def rupee(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def money2(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def to_csv(rows: list[Decision]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.as_dict())
    return buffer.getvalue()


def _sort_key(line: PRLine):
    return (line.created_at, line.pr_no, line.pr_line)


def _blank(line: PRLine, **overrides) -> Decision:
    base = dict(
        pr_no=line.pr_no,
        pr_line=line.pr_line,
        resolved_item_code=line.resolved_item or "NEW",
        group_id="",
        po_id="",
        decision="",
        route="",
        supplier_id="",
        unit_price="",
        group_value_inr="",
        suggested_supplier_ids="",
        policy_version="",
        reason="",
    )
    base.update(overrides)
    return Decision(**base)


def process(lines: list[PRLine], catalog: Catalog, policies: list[dict]) -> list[Decision]:
    matcher = Matcher(catalog)
    for index, line in enumerate(lines):
        line.source_index = index  # type: ignore[attr-defined]
        matcher.apply(line)

    skipped: dict[int, Decision] = {}
    workable: list[PRLine] = []
    for line in lines:
        policy = policy_for(policies, line.created_at.date())
        if line.plant not in catalog.plants:
            skipped[line.source_index] = _blank(
                line,
                decision="NOT_PROCESSED",
                route="NOT_PROCESSED",
                policy_version=policy["version"],
                reason=f"Plant {line.plant} is not in the plant master. Policy {policy['version']}.",
            )
            continue
        if line.quantity <= 0:
            skipped[line.source_index] = _blank(
                line,
                decision="NOT_PROCESSED",
                route="NOT_PROCESSED",
                policy_version=policy["version"],
                reason=f"Quantity is {line.quantity}. There is nothing to buy. Policy {policy['version']}.",
            )
            continue
        if line.resolved_item is None and line.estimate is None:
            skipped[line.source_index] = _blank(
                line,
                resolved_item_code="NEW",
                decision="NOT_PROCESSED",
                route="NOT_PROCESSED",
                policy_version=policy["version"],
                reason=f"New item with no estimated price. Policy {policy['version']}.",
            )
            continue
        workable.append(line)

    workable.sort(key=_sort_key)
    requirements: list[Requirement] = []
    for line in workable:
        policy = policy_for(policies, line.created_at.date())
        params = policy["parameters"]
        chosen = None
        if line.resolved_item is not None:
            for requirement in requirements:
                first = requirement.first
                if first.resolved_item != line.resolved_item or first.plant != line.plant:
                    continue
                if (line.created_at.date() - first.created_at.date()).days > int(params["club_window_days"]):
                    continue
                if abs((line.need_by - first.need_by).days) > int(params["need_by_window_days"]):
                    continue
                chosen = requirement
                break
        if chosen is None:
            requirement = Requirement(lines=[line], group_id=f"REQ-{len(requirements) + 1:04d}")
            requirements.append(requirement)
        else:
            chosen.lines.append(line)

    consumed = {contract.id: contract.consumed for contract in catalog.contracts}
    preferred_cache: dict[tuple[str, str], list[dict]] = {}
    open_pos: list[dict] = []
    po_seq = 0
    by_index: dict[int, Decision] = dict(skipped)

    for requirement in requirements:
        first = requirement.first
        req_date = first.created_at.date()
        policy = policy_for(policies, req_date)
        params = policy["parameters"]
        item_code = first.resolved_item
        item = catalog.items.get(item_code) if item_code else None
        quantity = sum((line.quantity for line in requirement.lines), Decimal("0"))
        emergency = any(line.urgency == "Emergency" for line in requirement.lines)

        applicable = []
        if item is not None:
            for contract in catalog.contracts:
                supplier = catalog.suppliers.get(contract.supplier_id)
                if supplier is None or supplier.status != "Active":
                    continue
                if not (contract.start <= req_date <= contract.end):
                    continue
                if item_code not in contract.rates:
                    continue
                applicable.append(contract)

        usable = []
        for contract in applicable:
            cost = quantity * contract.rates[item_code]
            if consumed[contract.id] + cost <= contract.cap:
                usable.append(contract)
        usable.sort(key=lambda contract: (contract.rates[item_code], contract.id))
        chosen_contract = usable[0] if usable else None

        history = None
        if item is not None:
            history = latest_active_purchase(
                catalog, item_code, req_date, int(params["history_days"])
            )

        def fallback() -> tuple[Decimal, str]:
            if applicable and not usable:
                lowest = min(applicable, key=lambda contract: (contract.rates[item_code], contract.id))
                return lowest.rates[item_code], f"lowest applicable contract rate on {lowest.id}"
            if history is not None:
                return history.unit_price, f"PO {history.number}"
            if item is not None:
                return item.standard_price, "item standard price"
            return first.estimate or Decimal("0"), "requester estimate"

        is_new = item is None
        is_capex = item is not None and item.item_type == "Capex"
        if is_new:
            valuation, value_source = first.estimate or Decimal("0"), "requester estimate"
        elif chosen_contract is not None and not is_capex:
            valuation = chosen_contract.rates[item_code]
            value_source = chosen_contract.id
        else:
            valuation, value_source = fallback()

        def facts_for(amount: Decimal, contract_ok: bool) -> dict:
            return {
                "item_type": item.item_type if item else "",
                "category": item.category if item else "",
                "plant": first.plant,
                "department": first.department,
                "value": amount,
                "urgency": "Emergency" if emergency else "Normal",
                "contract_usable": contract_ok,
                "bought_recently": history is not None,
                "item_is_new": is_new,
                "has_preferred": False,
            }

        group_value = rupee(quantity * valuation)
        preferred: list[dict] = []
        if item is not None:
            cache_key = (item_code, req_date.isoformat())
            if cache_key not in preferred_cache:
                preferred_cache[cache_key] = rank_item(catalog, item_code, req_date, policy)
            preferred = preferred_cache[cache_key]

        facts = facts_for(group_value, chosen_contract is not None)
        facts["has_preferred"] = bool(preferred)
        rule = first_rule(policy, facts)
        if (
            rule
            and rule["outcome"].get("price") != "contract_rate"
            and chosen_contract is not None
            and not is_capex
            and not is_new
        ):
            valuation, value_source = fallback()
            group_value = rupee(quantity * valuation)
            facts = facts_for(group_value, True)
            facts["has_preferred"] = bool(preferred)
            rule = first_rule(policy, facts)
            if rule and rule["outcome"].get("price") == "contract_rate":
                valuation = chosen_contract.rates[item_code]
                value_source = chosen_contract.id
                group_value = rupee(quantity * valuation)
                facts = facts_for(group_value, True)
                facts["has_preferred"] = bool(preferred)
                rule = first_rule(policy, facts)

        if rule is None:
            rule = {
                "id": "none",
                "outcome": {
                    "decision": "SOURCING_REQUEST",
                    "route": "RFQ",
                    "supplier": "none",
                    "price": "none",
                    "suggest": 3,
                },
            }

        outcome = rule["outcome"]
        route = outcome["route"]
        decision_name = outcome["decision"]
        supplier_id = ""
        unit_price = ""
        reference_note = ""

        if outcome.get("supplier") == "contract" and chosen_contract is not None:
            supplier_id = chosen_contract.supplier_id
            unit_price = money2(chosen_contract.rates[item_code])
            consumed[chosen_contract.id] += quantity * chosen_contract.rates[item_code]
        elif outcome.get("supplier") == "preferred_1" and preferred:
            top = preferred[0]
            supplier_id = top["supplier_id"]
            unit_price = money2(top["reference_price"])
            reference_note = f"Reference price from {top['reference_source']}."

        suggestions = ""
        count = suggest_count(policy, outcome)
        if count and item is not None and decision_name != "PO":
            suggestions = ";".join(suggest(catalog, item_code, req_date, policy, count))

        po_id = ""
        if decision_name == "PO" and supplier_id:
            hours = int(params["po_consolidation_hours"])
            placed = None
            for existing in open_pos:
                if (
                    existing["supplier_id"] == supplier_id
                    and existing["plant"] == first.plant
                    and existing["route"] == route
                ):
                    delta = first.created_at - existing["anchor"]
                    if timedelta(0) <= delta <= timedelta(hours=hours):
                        placed = existing
                        break
            if placed is None:
                po_seq += 1
                placed = {
                    "po_id": f"PO-{po_seq:04d}",
                    "supplier_id": supplier_id,
                    "plant": first.plant,
                    "route": route,
                    "anchor": first.created_at,
                }
                open_pos.append(placed)
            po_id = placed["po_id"]

        ceiling_note = ""
        if applicable and not usable and route != "CONTRACT_PO":
            names = ", ".join(contract.id for contract in applicable)
            ceiling_note = f"{names} has no ceiling left for this requirement."
        reason_parts = [f"Clause {rule['id']} {route}. Policy {policy['version']}."]
        if route == "CONTRACT_PO" and chosen_contract is not None:
            reason_parts.append(f"Contract {chosen_contract.id}.")
        if ceiling_note:
            reason_parts.append(ceiling_note)
        if reference_note:
            reason_parts.append(reference_note)
        if value_source and not value_source.startswith("RC-"):
            reason_parts.append(f"Requirement value from {value_source}.")
        elif value_source.startswith("RC-") and route != "CONTRACT_PO":
            reason_parts.append(f"Requirement value from {value_source}.")
        reason = " ".join(reason_parts)

        for line in requirement.lines:
            by_index[line.source_index] = Decision(
                pr_no=line.pr_no,
                pr_line=line.pr_line,
                resolved_item_code=line.resolved_item or "NEW",
                group_id=requirement.group_id,
                po_id=po_id,
                decision=decision_name,
                route=route,
                supplier_id=supplier_id,
                unit_price=unit_price,
                group_value_inr=str(int(group_value)),
                suggested_supplier_ids=suggestions,
                policy_version=policy["version"],
                reason=reason,
            )

    return [by_index[index] for index in range(len(lines))]
