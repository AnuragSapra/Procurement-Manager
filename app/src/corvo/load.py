from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from corvo.models import Catalog, Contract, Item, PRLine, Purchase, Supplier
from corvo.pdftext import extract_text, tidy

UOMS = (
    "NOS",
    "KG",
    "PAIR",
    "CYL",
    "LTR",
    "MTR",
    "SET",
    "ROL",
    "REAM",
    "JOB",
    "MON",
    "BKT",
    "BRL",
    "QTR",
)


def unit_label(code: str) -> str:
    if (code or "NOS").upper() == "NOS":
        return "pieces"
    return code


def kit_dir() -> Path:
    import os

    env = os.environ.get("CORVO_DATA")
    if env:
        return Path(env)
    # app/src/corvo/load.py -> repository root
    return Path(__file__).resolve().parents[3]


def _dec(value: str) -> Decimal:
    return Decimal(value.strip().replace(",", ""))


def load_items(path: Path) -> dict[str, Item]:
    items: dict[str, Item] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            items[row["item_code"]] = Item(
                code=row["item_code"],
                description=row["description"],
                uom=row["uom"],
                category=row["category"],
                item_type=row["item_type"],
                standard_price=_dec(row["standard_price"]),
            )
    return items


def load_suppliers(path: Path) -> dict[str, Supplier]:
    suppliers: dict[str, Supplier] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            suppliers[row["supplier_id"]] = Supplier(
                id=row["supplier_id"],
                name=row["name"],
                city=row["city"],
                categories=tuple(part.strip() for part in row["categories"].split(";") if part.strip()),
                status=row["status"],
                on_time=_dec(row["on_time_pct"]),
                quality=_dec(row["quality_rating"]),
            )
    return suppliers


def load_plants(path: Path) -> set[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["plant"] for row in csv.DictReader(handle)}


def load_purchases(path: Path) -> list[Purchase]:
    purchases: list[Purchase] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            purchases.append(
                Purchase(
                    number=row["po_number"],
                    number_i=int(row["po_number"]),
                    po_date=date.fromisoformat(row["po_date"]),
                    supplier_id=row["supplier_id"],
                    item_code=row["item_code"],
                    quantity=_dec(row["quantity"]),
                    uom=row["uom"],
                    unit_price=_dec(row["unit_price"]),
                    plant=row["plant"],
                )
            )
    return purchases


def load_register(register_path: Path, lines_path: Path) -> list[Contract]:
    rates: dict[str, dict[str, Decimal]] = {}
    with lines_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rates.setdefault(row["contract_id"], {})[row["item_code"]] = _dec(row["rate"])
    contracts: list[Contract] = []
    with register_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            contracts.append(
                Contract(
                    id=row["contract_id"],
                    supplier_id=row["supplier_id"],
                    supplier_name=row["supplier_name"],
                    start=date.fromisoformat(row["start_date"]),
                    end=date.fromisoformat(row["end_date"]),
                    cap=_dec(row["value_cap"]),
                    consumed=_dec(row["consumed_value"]),
                    rates=rates.get(row["contract_id"], {}),
                    source="register",
                )
            )
    return contracts


def parse_contract_pdf(path: Path) -> Contract:
    text = tidy(extract_text(path))
    number = re.search(r"Contract\s+No\.?\s*(RC-\d{4}-\d{3})", text)
    supplier = re.search(r"Supplier\s+ID\s*(S\d{3})", text)
    term = re.search(
        r"valid from\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})", text
    )
    ceiling = re.search(r"not exceed INR\s*([\d,]+)", text)
    if not (number and supplier and term and ceiling):
        raise ValueError(f"Could not read contract header from {path.name}")
    name_match = re.search(r"and\s+(.+?)\s*,\s*\w+\s*\(Supplier ID", text)
    rates: dict[str, Decimal] = {}
    uom_pattern = "|".join(UOMS)
    for item, _desc, _uom, rate in re.findall(
        rf"(ITM-\d+)\s+(.+?)\s+({uom_pattern})\s+([\d,]+\.\d{{2}})", text
    ):
        rates[item] = _dec(rate)
    if not rates:
        raise ValueError(f"Could not read schedule from {path.name}")
    return Contract(
        id=number.group(1),
        supplier_id=supplier.group(1),
        supplier_name=(name_match.group(1).strip() if name_match else ""),
        start=date.fromisoformat(term.group(1)),
        end=date.fromisoformat(term.group(2)),
        cap=_dec(ceiling.group(1)),
        consumed=Decimal("0"),
        rates=rates,
        source="pdf",
    )


def load_catalog(data_dir: Path | None = None) -> Catalog:
    root = data_dir or kit_dir()
    contracts = load_register(root / "contract_register.csv", root / "contract_lines.csv")
    known = {contract.id for contract in contracts}
    for path in sorted(root.glob("RC-*.pdf")):
        parsed = parse_contract_pdf(path)
        if parsed.id not in known:
            contracts.append(parsed)
            known.add(parsed.id)
    return Catalog(
        items=load_items(root / "item_master.csv"),
        suppliers=load_suppliers(root / "supplier_master.csv"),
        plants=load_plants(root / "plants.csv"),
        purchases=load_purchases(root / "po_history.csv"),
        contracts=contracts,
    )


def parse_pr_csv(text: str) -> list[PRLine]:
    lines: list[PRLine] = []
    reader = csv.DictReader(text.splitlines())
    required = ("pr_no", "pr_line", "created_at", "plant", "quantity", "need_by_date")
    fields = reader.fieldnames or []
    missing = [name for name in required if name not in fields]
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))
    for row in reader:
        estimate = row.get("estimated_unit_price", "").strip()
        code = row.get("item_code", "").strip()
        lines.append(
            PRLine(
                pr_no=row["pr_no"].strip(),
                pr_line=int(row["pr_line"]),
                created_at=datetime.strptime(row["created_at"].strip(), "%Y-%m-%d %H:%M"),
                requester=row.get("requester", "").strip(),
                department=row.get("department", "").strip(),
                plant=row["plant"].strip(),
                item_code=code,
                description=row.get("description", "").strip(),
                quantity=_dec(row["quantity"]),
                uom=row.get("uom", "").strip(),
                estimate=_dec(estimate) if estimate else None,
                need_by=date.fromisoformat(row["need_by_date"].strip()),
                urgency=row.get("urgency", "Normal").strip() or "Normal",
            )
        )
    if not lines:
        raise ValueError("CSV has no requisition lines")
    return lines


def load_pr_file(path: Path) -> list[PRLine]:
    return parse_pr_csv(path.read_text(encoding="utf-8-sig"))
