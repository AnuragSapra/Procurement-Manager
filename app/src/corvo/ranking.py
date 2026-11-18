from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from corvo.models import Catalog, Contract, Purchase, Supplier

RECENCY_FULL = Decimal("1")
RECENCY_HALF = Decimal("0.5")
RECENCY_NONE = Decimal("0")


def _weights(policy: dict) -> dict[str, Decimal]:
    params = policy["parameters"]
    return {key: Decimal(str(params[key])) for key in (
        "rank_po_weight",
        "rank_recency_weight",
        "rank_contract_weight",
        "rank_ontime_weight",
        "rank_quality_weight",
    )}


def recency(last: date, as_of: date, recent_days: int, year_days: int) -> Decimal:
    days = (as_of - last).days
    if days <= recent_days:
        return RECENCY_FULL
    if days <= year_days:
        return RECENCY_HALF
    return RECENCY_NONE


def _latest(purchases: list[Purchase]) -> Purchase:
    return max(purchases, key=lambda row: (row.po_date, row.number_i))


def rank_item(catalog: Catalog, item_code: str, as_of: date, policy: dict) -> list[dict]:
    params = policy["parameters"]
    weights = _weights(policy)
    lookback = int(params["preferred_lookback_days"])
    start = as_of - timedelta(days=lookback)
    by_supplier: dict[str, list[Purchase]] = {}
    for purchase in catalog.purchases:
        if purchase.item_code != item_code:
            continue
        if start <= purchase.po_date < as_of:
            by_supplier.setdefault(purchase.supplier_id, []).append(purchase)

    contract_rate: dict[str, tuple[date, Decimal, str]] = {}
    for contract in catalog.contracts:
        if contract.start > as_of or item_code not in contract.rates:
            continue
        current = contract_rate.get(contract.supplier_id)
        if current is None or contract.start > current[0]:
            contract_rate[contract.supplier_id] = (
                contract.start,
                contract.rates[item_code],
                contract.id,
            )

    ranked: list[dict] = []
    for supplier_id in set(by_supplier) | set(contract_rate):
        supplier = catalog.suppliers.get(supplier_id)
        if supplier is None or supplier.status != "Active":
            continue
        rows = by_supplier.get(supplier_id, [])
        if rows:
            latest = _latest(rows)
            bucket = recency(
                latest.po_date,
                as_of,
                int(params["recency_recent_days"]),
                int(params["recency_year_days"]),
            )
            reference = latest.unit_price
            source = latest.number
        else:
            bucket = RECENCY_NONE
            reference = contract_rate[supplier_id][1]
            source = contract_rate[supplier_id][2]
        has_contract = supplier_id in contract_rate
        score = (
            weights["rank_po_weight"] * len(rows)
            + weights["rank_recency_weight"] * bucket
            + weights["rank_contract_weight"] * (1 if has_contract else 0)
            + weights["rank_ontime_weight"] * supplier.on_time
            + weights["rank_quality_weight"] * supplier.quality
        )
        ranked.append(
            {
                "supplier_id": supplier_id,
                "supplier_name": supplier.name,
                "score": score,
                "po_count": len(rows),
                "recency": bucket,
                "has_contract": has_contract,
                "reference_price": reference,
                "reference_source": source,
                "on_time": supplier.on_time,
                "quality": supplier.quality,
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["supplier_id"]))
    return ranked


def category_fill(catalog: Catalog, item_code: str, exclude: set[str]) -> list[str]:
    item = catalog.items[item_code]
    rows: list[tuple[Decimal, str]] = []
    for supplier in catalog.suppliers.values():
        if supplier.status != "Active" or supplier.id in exclude:
            continue
        if item.category not in supplier.categories:
            continue
        rows.append((supplier.on_time * supplier.quality, supplier.id))
    rows.sort(key=lambda pair: (-pair[0], pair[1]))
    return [supplier_id for _, supplier_id in rows]


def preferred_table(catalog: Catalog, as_of: date, policy: dict, limit: int = 3) -> list[dict]:
    """Top preferred suppliers per item. The export uses the first four keys."""
    rows: list[dict] = []
    for item_code in sorted(catalog.items):
        ranked = rank_item(catalog, item_code, as_of, policy)
        if not ranked:
            rows.append(
                {
                    "item_code": item_code,
                    "rank": "",
                    "supplier_id": "",
                    "reference_price": "",
                    "supplier_name": "",
                    "po_count": 0,
                    "recency": "",
                    "has_contract": False,
                    "on_time": "",
                    "quality": "",
                    "why": "No Active supplier has a purchase or a started contract for this item in the window.",
                }
            )
            continue
        for rank, row in enumerate(ranked, start=1):
            if rank > limit:
                break
            price = row["reference_price"].quantize(Decimal("0.01"))
            contract = "a contract" if row["has_contract"] else "no contract"
            rows.append(
                {
                    "item_code": item_code,
                    "rank": rank,
                    "supplier_id": row["supplier_id"],
                    "reference_price": format(price, "f"),
                    "supplier_name": row["supplier_name"],
                    "po_count": row["po_count"],
                    "recency": format(row["recency"], "f"),
                    "has_contract": row["has_contract"],
                    "on_time": format(row["on_time"], "f"),
                    "quality": format(row["quality"], "f"),
                    "why": (
                        f"Score {format(row['score'], 'f')}: {row['po_count']} purchase orders, "
                        f"recency {format(row['recency'], 'f')} "
                        f"(1 is within 90 days, 0.5 within a year), {contract}, "
                        f"on-time {format(row['on_time'], 'f')}, quality {format(row['quality'], 'f')}."
                    ),
                }
            )
    return rows


def suggest(catalog: Catalog, item_code: str, as_of: date, policy: dict, count: int) -> list[str]:
    if count <= 0 or item_code not in catalog.items:
        return []
    preferred = rank_item(catalog, item_code, as_of, policy)
    ids = [row["supplier_id"] for row in preferred]
    if len(ids) < count:
        ids.extend(category_fill(catalog, item_code, set(ids)))
    return ids[:count]


def latest_active_purchase(
    catalog: Catalog, item_code: str, as_of: date, days: int, supplier_id: str | None = None
) -> Purchase | None:
    start = as_of - timedelta(days=days)
    best: Purchase | None = None
    for purchase in catalog.purchases:
        if purchase.item_code != item_code:
            continue
        if supplier_id and purchase.supplier_id != supplier_id:
            continue
        if not (start <= purchase.po_date < as_of):
            continue
        supplier = catalog.suppliers.get(purchase.supplier_id)
        if supplier is None or supplier.status != "Active":
            continue
        if best is None or (purchase.po_date, purchase.number_i) > (best.po_date, best.number_i):
            best = purchase
    return best
