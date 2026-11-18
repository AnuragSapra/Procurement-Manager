from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from corvo.engine import process
from corvo.load import kit_dir, load_catalog, load_pr_file
from corvo.models import Catalog, Contract, Item, PRLine, Purchase, Supplier
from corvo.policy import POLICY_V5, stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    code TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    uom TEXT NOT NULL,
    category TEXT NOT NULL,
    item_type TEXT NOT NULL,
    standard_price TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    categories TEXT NOT NULL,
    status TEXT NOT NULL,
    on_time TEXT NOT NULL,
    quality TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plants (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS po_history (
    number TEXT PRIMARY KEY,
    po_date TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    item_code TEXT NOT NULL,
    quantity TEXT NOT NULL,
    uom TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    plant TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    supplier_name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    cap TEXT NOT NULL,
    consumed TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract_lines (
    contract_id TEXT NOT NULL,
    item_code TEXT NOT NULL,
    rate TEXT NOT NULL,
    PRIMARY KEY (contract_id, item_code)
);
CREATE TABLE IF NOT EXISTS policies (
    version TEXT PRIMARY KEY,
    effective_from TEXT NOT NULL,
    status TEXT NOT NULL,
    document TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pr_lines (
    batch_id TEXT NOT NULL,
    source_index INTEGER NOT NULL,
    pr_no TEXT NOT NULL,
    pr_line INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    requester TEXT NOT NULL,
    department TEXT NOT NULL,
    plant TEXT NOT NULL,
    item_code TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity TEXT NOT NULL,
    uom TEXT NOT NULL,
    estimate TEXT,
    need_by TEXT NOT NULL,
    urgency TEXT NOT NULL,
    PRIMARY KEY (batch_id, source_index)
);
CREATE TABLE IF NOT EXISTS decisions (
    batch_id TEXT NOT NULL,
    source_index INTEGER NOT NULL,
    pr_no TEXT NOT NULL,
    pr_line INTEGER NOT NULL,
    resolved_item_code TEXT NOT NULL,
    group_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    route TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    group_value_inr TEXT NOT NULL,
    suggested_supplier_ids TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    override_comment TEXT NOT NULL,
    original_json TEXT NOT NULL,
    PRIMARY KEY (batch_id, source_index)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    draft_json TEXT NOT NULL,
    messages_json TEXT NOT NULL
);
"""


def db_path() -> Path:
    env = os.environ.get("CORVO_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "corvo.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def seed(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if count:
        return
    catalog = load_catalog()
    root = kit_dir()
    with root.joinpath("plants.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            conn.execute(
                "INSERT INTO plants (code, name) VALUES (?, ?)",
                (row["plant"], row["name"]),
            )
    for item in catalog.items.values():
        conn.execute(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
            (item.code, item.description, item.uom, item.category, item.item_type, str(item.standard_price)),
        )
    for supplier in catalog.suppliers.values():
        conn.execute(
            "INSERT INTO suppliers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                supplier.id,
                supplier.name,
                supplier.city,
                ";".join(supplier.categories),
                supplier.status,
                str(supplier.on_time),
                str(supplier.quality),
            ),
        )
    for purchase in catalog.purchases:
        conn.execute(
            "INSERT INTO po_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                purchase.number,
                purchase.po_date.isoformat(),
                purchase.supplier_id,
                purchase.item_code,
                str(purchase.quantity),
                purchase.uom,
                str(purchase.unit_price),
                purchase.plant,
            ),
        )
    _insert_contract(conn, catalog.contracts)
    policy = json.loads(json.dumps(POLICY_V5))
    policy["status"] = "approved"
    conn.execute(
        "INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            policy["version"],
            policy["effective_from"],
            "approved",
            json.dumps(policy),
            "Procurement Policy v5.0 as issued.",
            "seed",
            stamp(),
        ),
    )
    lines = load_pr_file(root / "sample_pr_batch.csv")
    decisions = process(lines, catalog, [policy])
    save_batch(conn, "sample", "sample_pr_batch.csv", "seed", "seed", lines, decisions)
    conn.commit()


def _insert_contract(conn: sqlite3.Connection, contracts: list[Contract]) -> None:
    for contract in contracts:
        conn.execute(
            "INSERT INTO contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contract.id,
                contract.supplier_id,
                contract.supplier_name,
                contract.start.isoformat(),
                contract.end.isoformat(),
                str(contract.cap),
                str(contract.consumed),
                contract.source,
            ),
        )
        for item_code, rate in contract.rates.items():
            conn.execute(
                "INSERT INTO contract_lines VALUES (?, ?, ?)",
                (contract.id, item_code, str(rate)),
            )


def add_contract(conn: sqlite3.Connection, contract: Contract) -> None:
    existing = conn.execute("SELECT id FROM contracts WHERE id = ?", (contract.id,)).fetchone()
    if existing:
        raise ValueError(f"{contract.id} is already in the register.")
    _insert_contract(conn, [contract])
    conn.commit()


def catalog_from(conn: sqlite3.Connection) -> Catalog:
    items = {
        row["code"]: Item(
            row["code"],
            row["description"],
            row["uom"],
            row["category"],
            row["item_type"],
            Decimal(row["standard_price"]),
        )
        for row in conn.execute("SELECT * FROM items")
    }
    suppliers = {}
    for row in conn.execute("SELECT * FROM suppliers"):
        categories = tuple(part.strip() for part in row["categories"].split(";") if part.strip())
        suppliers[row["id"]] = Supplier(
            row["id"],
            row["name"],
            row["city"],
            categories,
            row["status"],
            Decimal(row["on_time"]),
            Decimal(row["quality"]),
        )
    plants = {row["code"] for row in conn.execute("SELECT code FROM plants")}
    purchases = []
    for row in conn.execute("SELECT * FROM po_history"):
        purchases.append(
            Purchase(
                number=row["number"],
                number_i=int(row["number"]),
                po_date=date.fromisoformat(row["po_date"]),
                supplier_id=row["supplier_id"],
                item_code=row["item_code"],
                quantity=Decimal(row["quantity"]),
                uom=row["uom"],
                unit_price=Decimal(row["unit_price"]),
                plant=row["plant"],
            )
        )
    rates: dict[str, dict[str, Decimal]] = {}
    for row in conn.execute("SELECT * FROM contract_lines"):
        rates.setdefault(row["contract_id"], {})[row["item_code"]] = Decimal(row["rate"])
    contracts = []
    for row in conn.execute("SELECT * FROM contracts ORDER BY id"):
        contracts.append(
            Contract(
                id=row["id"],
                supplier_id=row["supplier_id"],
                supplier_name=row["supplier_name"],
                start=date.fromisoformat(row["start_date"]),
                end=date.fromisoformat(row["end_date"]),
                cap=Decimal(row["cap"]),
                consumed=Decimal(row["consumed"]),
                rates=rates.get(row["id"], {}),
                source=row["source"],
            )
        )
    return Catalog(items, suppliers, plants, purchases, contracts)


def plant_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["code"]: row["name"] for row in conn.execute("SELECT code, name FROM plants")}


def policies(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql = "SELECT document FROM policies"
    args: tuple = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    sql += " ORDER BY effective_from, version"
    return [json.loads(row["document"]) for row in conn.execute(sql, args)]


def latest_approved(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT document FROM policies WHERE status = 'approved' ORDER BY effective_from DESC, version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("No approved policy.")
    return json.loads(row["document"])


def pending_policy(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT document, notes FROM policies WHERE status = 'pending' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    document = json.loads(row["document"])
    document["_notes"] = [line for line in row["notes"].split("\n") if line]
    return document


def save_pending(conn: sqlite3.Connection, policy: dict, notes: list[str], actor: str) -> None:
    conn.execute("DELETE FROM policies WHERE status = 'pending'")
    conn.execute(
        "INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            policy["version"],
            policy["effective_from"],
            "pending",
            json.dumps(policy),
            "\n".join(notes),
            actor,
            stamp(),
        ),
    )
    conn.commit()


def approve_policy(conn: sqlite3.Connection, actor: str) -> dict:
    row = conn.execute(
        "SELECT version, document, notes FROM policies WHERE status = 'pending' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("There is no policy waiting for approval.")
    document = json.loads(row["document"])
    if any(line.startswith("No effective date") for line in row["notes"].split("\n")):
        raise ValueError("Add an effective date before approval.")
    document["status"] = "approved"
    conn.execute(
        "UPDATE policies SET status = 'approved', document = ?, created_by = ?, created_at = ? WHERE version = ?",
        (json.dumps(document), actor, stamp(), row["version"]),
    )
    reprocess_all(conn)
    audit(conn, actor, "buyer", "approve_policy", document["version"])
    conn.commit()
    return document


def save_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    name: str,
    source: str,
    actor: str,
    lines: list[PRLine],
    decisions,
) -> None:
    now = stamp()
    existing = conn.execute("SELECT created_at, created_by FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE batches SET name = ?, source = ?, processed_at = ? WHERE id = ?",
            (name, source, now, batch_id),
        )
    else:
        conn.execute(
            "INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, name, source, actor, now, now),
        )
    conn.execute("DELETE FROM pr_lines WHERE batch_id = ?", (batch_id,))
    conn.execute("DELETE FROM decisions WHERE batch_id = ?", (batch_id,))
    for index, line in enumerate(lines):
        conn.execute(
            "INSERT INTO pr_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_id,
                index,
                line.pr_no,
                line.pr_line,
                line.created_at.strftime("%Y-%m-%d %H:%M"),
                line.requester,
                line.department,
                line.plant,
                line.item_code,
                line.description,
                str(line.quantity),
                line.uom,
                None if line.estimate is None else str(line.estimate),
                line.need_by.isoformat(),
                line.urgency,
            ),
        )
    for index, decision in enumerate(decisions):
        conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_id,
                index,
                decision.pr_no,
                decision.pr_line,
                decision.resolved_item_code,
                decision.group_id,
                decision.po_id,
                decision.decision,
                decision.route,
                decision.supplier_id,
                decision.unit_price,
                decision.group_value_inr,
                decision.suggested_supplier_ids,
                decision.policy_version,
                decision.reason,
                "pending",
                "",
                "",
            ),
        )


def lines_of(conn: sqlite3.Connection, batch_id: str) -> list[PRLine]:
    rows = conn.execute(
        "SELECT * FROM pr_lines WHERE batch_id = ? ORDER BY source_index", (batch_id,)
    ).fetchall()
    lines = []
    for row in rows:
        lines.append(
            PRLine(
                pr_no=row["pr_no"],
                pr_line=row["pr_line"],
                created_at=datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M"),
                requester=row["requester"],
                department=row["department"],
                plant=row["plant"],
                item_code=row["item_code"],
                description=row["description"],
                quantity=Decimal(row["quantity"]),
                uom=row["uom"],
                estimate=Decimal(row["estimate"]) if row["estimate"] else None,
                need_by=date.fromisoformat(row["need_by"]),
                urgency=row["urgency"],
            )
        )
    return lines


def reprocess(conn: sqlite3.Connection, batch_id: str) -> list:
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        return []
    lines = lines_of(conn, batch_id)
    decisions = process(lines, catalog_from(conn), policies(conn, "approved"))
    save_batch(conn, batch_id, row["name"], row["source"], row["created_by"], lines, decisions)
    return decisions


def reprocess_all(conn: sqlite3.Connection) -> None:
    ids = [row["id"] for row in conn.execute("SELECT id FROM batches")]
    for batch_id in ids:
        reprocess(conn, batch_id)


def decision_rows(conn: sqlite3.Connection, batch_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM decisions WHERE batch_id = ? ORDER BY source_index", (batch_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def next_chat_number(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT pr_no FROM pr_lines WHERE pr_no LIKE 'PR-CHAT-%' ORDER BY pr_no DESC LIMIT 1"
    ).fetchone()
    number = 1 if row is None else int(row["pr_no"].rsplit("-", 1)[1]) + 1
    return f"PR-CHAT-{number:05d}"


def audit(conn: sqlite3.Connection, actor: str, role: str, action: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO audit (at, actor, role, action, detail) VALUES (?, ?, ?, ?, ?)",
        (stamp(), actor, role, action, detail),
    )


def recent_audit(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT at, actor, role, action, detail FROM audit ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def chat_state(conn: sqlite3.Connection, session_id: str) -> tuple[dict, list[dict]]:
    row = conn.execute("SELECT draft_json, messages_json FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return {}, []
    return json.loads(row["draft_json"]), json.loads(row["messages_json"])


def save_chat(conn: sqlite3.Connection, session_id: str, draft: dict, messages: list[dict]) -> None:
    conn.execute(
        """
        INSERT INTO chat_sessions (id, draft_json, messages_json) VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET draft_json = excluded.draft_json, messages_json = excluded.messages_json
        """,
        (session_id, json.dumps(draft), json.dumps(messages)),
    )
    conn.commit()
