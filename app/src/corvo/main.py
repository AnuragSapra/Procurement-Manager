from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import tempfile
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from corvo.auth import (
    PUBLIC_PEOPLE,
    SESSION_SECONDS,
    check_csv,
    check_pdf,
    check_text,
    identify,
    session_secret,
    tokens_match,
)
from corvo.chat import fresh_draft, looks_like_policy, turn
from corvo.load import unit_label
from corvo.db import (
    add_contract,
    approve_policy,
    audit,
    catalog_from,
    chat_state,
    connect,
    decision_rows,
    init,
    latest_approved,
    lines_of,
    next_chat_number,
    pending_policy,
    plant_names,
    policies,
    recent_audit,
    reprocess,
    save_batch,
    save_chat,
    save_pending,
    seed,
)
from corvo.engine import EXPORT_COLUMNS, Decision, to_csv
from corvo.load import parse_contract_pdf, parse_pr_csv
from corvo.policy import explain_policy, parse_amendment, policy_for
from corvo.ranking import preferred_table

APP_DIR = Path(__file__).resolve().parents[2]
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))
TEMPLATES.env.auto_reload = True
STATIC = APP_DIR / "static"

PEOPLE = PUBLIC_PEOPLE
_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _inr(value: str) -> str:
    if value in (None, ""):
        return ""
    return f"₹{int(value):,}"


TEMPLATES.env.filters["inr"] = _inr
TEMPLATES.env.filters["unit"] = unit_label


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = connect()
    try:
        init(conn)
        seed(conn)
    finally:
        conn.close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Corvo PR to PO", lifespan=lifespan)
    secret = session_secret()
    production = os.environ.get("CORVO_ENV") == "production"
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    _routes(app)

    @app.middleware("http")
    async def require_sign_in(request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path == "/who":
            return await call_next(request)
        if _person(request) is None:
            if path.startswith("/api"):
                return JSONResponse({"error": "Sign in required."}, status_code=401)
            return RedirectResponse("/who", status_code=303)
        return await call_next(request)

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="lax",
        https_only=production,
        max_age=SESSION_SECONDS,
    )
    return app


def _person(request: Request) -> dict | None:
    if request.session.get("signed_in") is not True:
        return None
    name = request.session.get("name")
    for candidate in PEOPLE:
        if candidate["name"] == name:
            return candidate
    return None


def _ctx(request: Request, **extra):
    extra["request"] = request
    extra["who"] = _person(request)
    extra["people"] = PEOPLE
    extra["csrf"] = _ensure_csrf(request)
    extra["flash"] = request.session.pop("flash", "")
    return extra


def _ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _form_ok(request: Request, form) -> bool:
    return tokens_match(_ensure_csrf(request), str(form.get("csrf") or ""))


def _header_ok(request: Request) -> bool:
    return tokens_match(str(request.session.get("csrf") or ""), request.headers.get("x-csrf-token", ""))


def _expired(request: Request):
    _flash(request, "That form expired. Try again.")
    return RedirectResponse(_back(request), status_code=303)


def _flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _back(request: Request) -> str:
    path = urlparse(request.headers.get("referer") or "/").path or "/"
    if not path.startswith("/"):
        return "/"
    return path


def _buyer(request: Request, message: str = "A requester cannot approve.") -> dict | Response:
    person = _person(request)
    if person is None:
        return Response("Sign in required.", status_code=401)
    if person["role"] != "buyer":
        return Response(message, status_code=403)
    return person


def _own_requirement(conn, batch_id: str, group_id: str, name: str) -> bool:
    rows = conn.execute(
        """
        SELECT p.requester
        FROM decisions d
        JOIN pr_lines p ON p.batch_id = d.batch_id AND p.source_index = d.source_index
        WHERE d.batch_id = ? AND d.group_id = ?
        """,
        (batch_id, group_id),
    ).fetchall()
    return any(row["requester"] == name for row in rows)


def _safe_batch_id(value: str) -> str | None:
    if _BATCH_ID.fullmatch(value or ""):
        return value
    return None


def _stored(rows: list[dict]) -> list[Decision]:
    decisions = []
    for row in rows:
        decisions.append(
            Decision(
                pr_no=row["pr_no"],
                pr_line=int(row["pr_line"]),
                resolved_item_code=row["resolved_item_code"],
                group_id=row["group_id"],
                po_id=row["po_id"],
                decision=row["decision"],
                route=row["route"],
                supplier_id=row["supplier_id"],
                unit_price=row["unit_price"],
                group_value_inr=row["group_value_inr"],
                suggested_supplier_ids=row["suggested_supplier_ids"],
                policy_version=row["policy_version"],
                reason=row["reason"],
            )
        )
    return decisions


def _requirements(conn, batch_id: str | None = None) -> list[dict]:
    sql = """
        SELECT d.*, p.description, p.quantity, p.uom, p.plant, p.need_by, p.urgency,
               p.requester, p.department, p.created_at AS line_created, b.name AS batch_name
        FROM decisions d
        JOIN pr_lines p ON p.batch_id = d.batch_id AND p.source_index = d.source_index
        JOIN batches b ON b.id = d.batch_id
    """
    args: tuple = ()
    if batch_id:
        sql += " WHERE d.batch_id = ?"
        args = (batch_id,)
    sql += " ORDER BY d.batch_id, d.source_index"
    grouped: dict[tuple[str, str], list] = {}
    loose: list[dict] = []
    catalog = catalog_from(conn)
    for row in conn.execute(sql, args):
        item = dict(row)
        code = item["resolved_item_code"]
        item["item_name"] = catalog.items[code].description if code in catalog.items else item["description"]
        supplier = catalog.suppliers.get(item["supplier_id"])
        item["supplier_name"] = supplier.name if supplier else ""
        if not item["group_id"]:
            loose.append(item)
            continue
        grouped.setdefault((item["batch_id"], item["group_id"]), []).append(item)
    cards = []
    for (bid, group_id), lines in grouped.items():
        first = lines[0]
        statuses = {line["status"] for line in lines}
        if "overridden" in statuses:
            status = "overridden"
        elif statuses == {"approved"}:
            status = "approved"
        else:
            status = "pending"
        cards.append(
            {
                "batch_id": bid,
                "batch_name": first["batch_name"],
                "group_id": group_id,
                "lines": lines,
                "status": status,
                "route": first["route"],
                "value": first["group_value_inr"],
                "supplier_id": first["supplier_id"],
                "supplier_name": first["supplier_name"],
                "suggestions": first["suggested_supplier_ids"],
                "po_id": first["po_id"],
                "unit_price": first["unit_price"],
                "decision": first["decision"],
                "policy_version": first["policy_version"],
                "reason": first["reason"],
                "item_name": first["item_name"],
                "plant": first["plant"],
                "urgency": first["urgency"],
            }
        )
    cards.sort(key=lambda card: (card["status"] != "pending", card["batch_id"], card["group_id"]))
    return cards, loose


def _routes(app: FastAPI) -> None:
    @app.get("/")
    def home(request: Request):
        if _person(request) is None:
            return RedirectResponse("/who", status_code=303)
        return RedirectResponse("/chat", status_code=303)

    @app.get("/who")
    def who_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "sign-in.html", _ctx(request))

    @app.post("/who")
    async def who(request: Request):
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        name = str(form.get("name") or "")
        password = str(form.get("password") or form.get("passphrase") or "")
        person = identify(name, password)
        conn = connect()
        try:
            if person is None:
                audit(conn, name or "unknown", "unknown", "sign_in_failed", "Refused a sign-in.")
                conn.commit()
                _flash(request, "That password does not match.")
                return RedirectResponse("/who", status_code=303)
            chats = dict(request.session.get("chat_ids") or {})
            request.session.clear()
            request.session["signed_in"] = True
            request.session["name"] = person["name"]
            request.session["chat_ids"] = chats
            request.session["csrf"] = secrets.token_urlsafe(32)
            audit(conn, person["name"], person["role"], "sign_in", "Signed in.")
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse("/chat", status_code=303)

    @app.post("/sign-out")
    async def sign_out(request: Request):
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        person = _person(request)
        chats = dict(request.session.get("chat_ids") or {})
        request.session.clear()
        request.session["chat_ids"] = chats
        request.session["csrf"] = secrets.token_urlsafe(32)
        if person is not None:
            conn = connect()
            try:
                audit(conn, person["name"], person["role"], "sign_out", "Signed out.")
                conn.commit()
            finally:
                conn.close()
        return RedirectResponse("/who", status_code=303)

    @app.get("/chat")
    def chat_page(request: Request):
        conn = connect()
        try:
            draft, messages = chat_state(conn, _chat_id(request))
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request, "chat.html", _ctx(request, messages=messages, draft=draft)
        )

    @app.post("/chat")
    async def chat_send(request: Request):
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        text = str(form.get("message") or "")
        if len(text) > 4000:
            _flash(request, "That message is too long.")
            return RedirectResponse("/chat", status_code=303)
        person = _person(request)
        if person is None:
            return RedirectResponse("/who", status_code=303)
        conn = connect()
        try:
            session_id = _chat_id(request)
            draft, messages = chat_state(conn, session_id)
            catalog = catalog_from(conn)
            now = datetime.now()
            reset = False
            if looks_like_policy(text):
                reply = _policy_in_chat(conn, person, text)
                result_draft = draft
            else:
                result = turn(
                    text,
                    draft,
                    catalog,
                    now.date(),
                    person["name"],
                    person["department"],
                    now,
                )
                reply = result.reply
                result_draft = result.draft
                reset = result.reset_transcript
                if result.line is not None:
                    result.line.pr_no = next_chat_number(conn)
                    existing = lines_of(conn, "chat")
                    existing.append(result.line)
                    decisions = process_chat(conn, existing, person["name"])
                    made = next(row for row in decisions if row.pr_no == result.line.pr_no)
                    reply = _decision_sentence(made, catalog)
                    audit(conn, person["name"], person["role"], "chat_pr", made.pr_no)
                    conn.commit()
            if reset:
                save_chat(conn, session_id, fresh_draft(), [])
            else:
                messages.append({"role": "user", "text": text})
                messages.append({"role": "assistant", "text": reply})
                save_chat(conn, session_id, result_draft, messages[-40:])
        finally:
            conn.close()
        return RedirectResponse("/chat", status_code=303)

    @app.get("/batch")
    def batch_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "batch.html", _ctx(request, summary=None))

    @app.post("/batch")
    async def batch_upload(request: Request):
        person = _buyer(request, "A requester cannot run a batch.")
        if isinstance(person, Response):
            return person
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            _flash(request, "Choose a CSV file.")
            return RedirectResponse("/batch", status_code=303)
        raw = await upload.read()
        try:
            check_csv(raw, upload.filename or "")
            lines = parse_pr_csv(raw.decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            _flash(request, f"Could not read that CSV ({exc}).")
            return RedirectResponse("/batch", status_code=303)
        batch_id = "upload-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        conn = connect()
        try:
            decisions = _run(conn, batch_id, upload.filename, "upload", person["name"], lines)
            conn.commit()
            counts = Counter(row.route for row in decisions)
        finally:
            conn.close()
        summary = {"batch_id": batch_id, "lines": len(decisions), "counts": dict(counts)}
        return TEMPLATES.TemplateResponse(request, "batch.html", _ctx(request, summary=summary))

    @app.get("/queue")
    def queue_page(request: Request):
        conn = connect()
        try:
            cards, loose = _requirements(conn)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request, "queue.html", _ctx(request, cards=cards, loose=loose)
        )

    @app.get("/queue/{batch_id}/{group_id}")
    def requirement_page(request: Request, batch_id: str, group_id: str):
        conn = connect()
        try:
            cards, _loose = _requirements(conn, batch_id)
        finally:
            conn.close()
        card = next((item for item in cards if item["group_id"] == group_id), None)
        if card is None:
            return Response("No such requirement.", status_code=404)
        person = _person(request)
        card["own"] = person is not None and any(line["requester"] == person["name"] for line in card["lines"])
        return TEMPLATES.TemplateResponse(
            request, "requirement.html", _ctx(request, card=card)
        )

    @app.post("/queue/{batch_id}/{group_id}/approve")
    async def approve(request: Request, batch_id: str, group_id: str):
        person = _buyer(request)
        if isinstance(person, Response):
            return person
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        conn = connect()
        try:
            found = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE batch_id = ? AND group_id = ?",
                (batch_id, group_id),
            ).fetchone()[0]
            if not found:
                return Response("No such requirement.", status_code=404)
            if _own_requirement(conn, batch_id, group_id, person["name"]):
                audit(conn, person["name"], person["role"], "rejected_self_approval", f"{batch_id} {group_id}")
                conn.commit()
                _flash(request, "You raised this requirement, so you cannot release it.")
                return RedirectResponse(f"/queue/{batch_id}/{group_id}", status_code=303)
            conn.execute(
                "UPDATE decisions SET status = 'approved' WHERE batch_id = ? AND group_id = ?",
                (batch_id, group_id),
            )
            audit(conn, person["name"], person["role"], "approve", f"{batch_id} {group_id}")
            conn.commit()
        finally:
            conn.close()
        _flash(request, f"Approved {group_id}.")
        return RedirectResponse(f"/queue/{batch_id}/{group_id}", status_code=303)

    @app.post("/queue/{batch_id}/{group_id}/override")
    async def override(request: Request, batch_id: str, group_id: str):
        person = _buyer(request)
        if isinstance(person, Response):
            return person
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        comment = str(form.get("comment") or "").strip()
        if not comment:
            _flash(request, "An override needs a comment.")
            return RedirectResponse(f"/queue/{batch_id}/{group_id}", status_code=303)
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE batch_id = ? AND group_id = ?",
                (batch_id, group_id),
            ).fetchall()
            if not rows:
                return Response("No such requirement.", status_code=404)
            if _own_requirement(conn, batch_id, group_id, person["name"]):
                audit(conn, person["name"], person["role"], "rejected_self_approval", f"{batch_id} {group_id}")
                conn.commit()
                _flash(request, "You raised this requirement, so you cannot release it.")
                return RedirectResponse(f"/queue/{batch_id}/{group_id}", status_code=303)
            for row in rows:
                original = row["original_json"] or _original(row)
                conn.execute(
                    """
                    UPDATE decisions
                    SET status = 'overridden', override_comment = ?, original_json = ?
                    WHERE batch_id = ? AND source_index = ?
                    """,
                    (comment, original, batch_id, row["source_index"]),
                )
            audit(conn, person["name"], person["role"], "override", f"{batch_id} {group_id}: {comment}")
            conn.commit()
        finally:
            conn.close()
        _flash(request, f"Override recorded on {group_id}. The original decision is unchanged.")
        return RedirectResponse(f"/queue/{batch_id}/{group_id}", status_code=303)

    @app.get("/policy")
    def policy_page(request: Request):
        conn = connect()
        try:
            current = latest_approved(conn)
            waiting = pending_policy(conn)
            history = policies(conn)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request,
            "policy.html",
            _ctx(
                request,
                current_lines=explain_policy(current),
                current=current,
                waiting=waiting,
                history=history,
                blocked=_blocked(waiting),
            ),
        )

    @app.post("/policy/preview")
    async def policy_preview(request: Request):
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        upload = form.get("file")
        text = str(form.get("change") or "")
        if upload is not None and getattr(upload, "filename", ""):
            raw = await upload.read()
            try:
                check_text(raw)
                text = raw.decode("utf-8-sig")
            except (UnicodeDecodeError, ValueError) as exc:
                _flash(request, f"Could not read that change ({exc}).")
                return RedirectResponse("/policy", status_code=303)
        person = _person(request)
        conn = connect()
        try:
            try:
                updated, notes = parse_amendment(text, latest_approved(conn))
            except (ValueError, KeyError) as exc:
                _flash(request, f"Could not read that change ({exc}).")
                return RedirectResponse("/policy", status_code=303)
            save_pending(conn, updated, notes, person["name"])
            audit(conn, person["name"], person["role"], "preview_policy", updated["version"])
            conn.commit()
        finally:
            conn.close()
        _flash(request, "Preview ready. Nothing is active until a buyer approves it.")
        return RedirectResponse("/policy", status_code=303)

    @app.post("/policy/approve")
    async def policy_approve(request: Request):
        person = _buyer(request)
        if isinstance(person, Response):
            return person
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        conn = connect()
        try:
            try:
                approved = approve_policy(conn, person["name"])
            except ValueError as exc:
                _flash(request, str(exc))
                return RedirectResponse("/policy", status_code=303)
        finally:
            conn.close()
        _flash(request, f"Policy {approved['version']} is active from {approved['effective_from']}.")
        return RedirectResponse("/policy", status_code=303)

    @app.get("/contracts")
    def contracts_page(request: Request):
        conn = connect()
        try:
            catalog = catalog_from(conn)
            names = plant_names(conn)
        finally:
            conn.close()
        rows = []
        for contract in sorted(catalog.contracts, key=lambda item: item.id):
            rows.append(
                {
                    "id": contract.id,
                    "supplier_id": contract.supplier_id,
                    "supplier_name": contract.supplier_name,
                    "start": contract.start.isoformat(),
                    "end": contract.end.isoformat(),
                    "cap": f"{int(contract.cap):,}",
                    "consumed": f"{int(contract.consumed):,}",
                    "source": contract.source,
                    "lines": len(contract.rates),
                }
            )
        return TEMPLATES.TemplateResponse(
            request, "contracts.html", _ctx(request, contracts=rows, plants=names)
        )

    @app.post("/contracts")
    async def contracts_upload(request: Request):
        person = _buyer(request, "A requester cannot add a contract.")
        if isinstance(person, Response):
            return person
        form = await request.form()
        if not _form_ok(request, form):
            return _expired(request)
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            _flash(request, "Choose a contract PDF.")
            return RedirectResponse("/contracts", status_code=303)
        data = await upload.read()
        try:
            check_pdf(data, upload.filename or "")
        except ValueError as exc:
            _flash(request, str(exc))
            return RedirectResponse("/contracts", status_code=303)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(data)
        tmp.close()
        path = Path(tmp.name)
        conn = connect()
        try:
            try:
                contract = parse_contract_pdf(path)
                add_contract(conn, contract)
            except ValueError as exc:
                _flash(request, str(exc))
                return RedirectResponse("/contracts", status_code=303)
            audit(conn, person["name"], person["role"], "add_contract", contract.id)
            conn.commit()
        finally:
            conn.close()
            path.unlink(missing_ok=True)
        _flash(request, f"Added {contract.id}.")
        return RedirectResponse("/contracts", status_code=303)

    @app.get("/preferred")
    def preferred_page(request: Request, as_of: str = "2026-11-01"):
        try:
            on = date.fromisoformat(as_of)
        except ValueError:
            _flash(request, "Use a date like 2026-11-01.")
            return RedirectResponse("/preferred", status_code=303)
        conn = connect()
        try:
            catalog = catalog_from(conn)
            policy = policy_for(policies(conn, "approved"), on)
            rows = preferred_table(catalog, on, policy)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request, "preferred.html", _ctx(request, as_of=as_of, rows=rows, policy_version=policy["version"])
        )

    @app.get("/audit")
    def audit_page(request: Request):
        person = _buyer(request, "A requester cannot read the audit log.")
        if isinstance(person, Response):
            return person
        conn = connect()
        try:
            rows = recent_audit(conn)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "audit.html", _ctx(request, rows=rows))

    @app.post("/api/process-batch")
    async def process_batch(request: Request, format: str = "json", batch_id: str = ""):
        person = _buyer(request, "A requester cannot run a batch.")
        if isinstance(person, Response):
            return person
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" in content_type:
            form = await request.form()
            token = str(form.get("csrf") or request.headers.get("x-csrf-token") or "")
            if not tokens_match(str(request.session.get("csrf") or ""), token):
                return JSONResponse({"error": "That form expired. Try again."}, status_code=403)
            upload = form.get("file")
            if upload is None:
                return JSONResponse({"error": "Attach the CSV as file."}, status_code=400)
            raw = await upload.read()
            filename = upload.filename or ""
        else:
            if not _header_ok(request):
                return JSONResponse({"error": "That form expired. Try again."}, status_code=403)
            raw = await request.body()
            filename = ""
        chosen = _safe_batch_id(batch_id) if batch_id else "api-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        if chosen is None:
            return JSONResponse({"error": "batch_id has unsupported characters."}, status_code=400)
        try:
            check_csv(raw, filename)
            lines = parse_pr_csv(raw.decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            return JSONResponse({"error": f"Could not read that CSV ({exc})."}, status_code=400)
        conn = connect()
        try:
            decisions = _run(conn, chosen, "upload.csv", "api", person["name"], lines)
            audit(conn, person["name"], person["role"], "process_batch", chosen)
            conn.commit()
        finally:
            conn.close()
        if format == "csv":
            return Response(
                to_csv(decisions),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{chosen}.csv"'},
            )
        return [row.as_dict() for row in decisions]

    @app.get("/api/batches/{batch_id}")
    def batch_json(batch_id: str, format: str = "json"):
        if _safe_batch_id(batch_id) is None:
            return JSONResponse({"error": "batch_id has unsupported characters."}, status_code=400)
        conn = connect()
        try:
            rows = decision_rows(conn, batch_id)
        finally:
            conn.close()
        if not rows:
            return JSONResponse({"error": "No such batch."}, status_code=404)
        decisions = _stored(rows)
        if format == "csv":
            return Response(
                to_csv(decisions),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{batch_id}.csv"'},
            )
        return [row.as_dict() for row in decisions]

    @app.get("/api/preferred-suppliers.csv")
    def preferred_csv(as_of: str = ""):
        if not as_of:
            return JSONResponse({"error": "as_of is required (YYYY-MM-DD)."}, status_code=400)
        try:
            on = date.fromisoformat(as_of)
        except ValueError:
            return JSONResponse({"error": "as_of must be YYYY-MM-DD."}, status_code=400)
        conn = connect()
        try:
            catalog = catalog_from(conn)
            policy = policy_for(policies(conn, "approved"), on)
            rows = preferred_table(catalog, on, policy)
        finally:
            conn.close()
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["item_code", "rank", "supplier_id", "reference_price"],
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(row for row in rows if row["supplier_id"])
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="preferred-suppliers.csv"'},
        )


def _policy_in_chat(conn, person: dict, text: str) -> str:
    if person["role"] != "buyer":
        return "Only procurement can change the policy. Tell me what you need to buy, or ask them to describe the change."
    try:
        updated, notes = parse_amendment(text, latest_approved(conn))
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return f"I could not read that policy change ({exc})."
    useful = [
        note
        for note in notes
        if not note.startswith("No effective date") and not note.startswith("The date was read")
    ]
    if not useful:
        return (
            "That sounds like a policy change, but I could not see a limit, a window, or a new rule. "
            "For example: Effective 1 Nov 2026, the spot PO limit is 50000."
        )
    save_pending(conn, updated, notes, person["name"])
    audit(conn, person["name"], person["role"], "preview_policy", updated["version"])
    conn.commit()
    shown = "\n".join(notes)
    return (
        "I read that as a policy change, not a requisition.\n"
        f"{shown}\n"
        "Nothing is active until you approve it on the policy page."
    )


def _chat_id(request: Request) -> str:
    person = _person(request)
    if person is None:
        raise RuntimeError("Sign in required.")
    ids = dict(request.session.get("chat_ids") or {})
    if person["name"] not in ids:
        ids[person["name"]] = uuid.uuid4().hex
        request.session["chat_ids"] = ids
    return ids[person["name"]]


def _run(conn, batch_id: str, name: str, source: str, actor: str, lines):
    decisions = process_lines(conn, lines)
    save_batch(conn, batch_id, name, source, actor, lines, decisions)
    return decisions


def process_lines(conn, lines):
    from corvo.engine import process

    return process(lines, catalog_from(conn), policies(conn, "approved"))


def process_chat(conn, lines, actor: str):
    decisions = process_lines(conn, lines)
    save_batch(conn, "chat", "Chat", "chat", actor, lines, decisions)
    return decisions


_ROUTE_PLAIN = {
    "CONTRACT_PO": "a purchase order on the existing contract",
    "EMERGENCY_PO": "an emergency purchase order",
    "SPOT_PO": "a spot purchase order",
    "RFQ": "a request for quotation",
    "RFP": "a request for proposal",
    "CAPEX_SOURCING": "a sourcing request for capital equipment",
    "NEW_ITEM_SOURCING": "a sourcing request for a new item",
    "NOT_PROCESSED": "not processed",
}

_ROUTE_WHY = {
    "CONTRACT_PO": "A current contract covers this item and still has room for the quantity.",
    "EMERGENCY_PO": "It is urgent, within the emergency limit, and this item was bought recently.",
    "SPOT_PO": "It is small enough for a spot order, and this item was bought recently.",
    "RFQ": "No contract or spot purchase applies, and the value is still below the proposal threshold.",
    "RFP": "The value is high enough that procurement asks suppliers for a proposal.",
    "CAPEX_SOURCING": "Capital equipment is sourced. It is not raised as a purchase order.",
    "NEW_ITEM_SOURCING": "This item is not in the catalogue, so procurement sources it.",
}


def _supplier_label(catalog, supplier_id: str) -> str:
    supplier = catalog.suppliers.get(supplier_id)
    if supplier is None:
        return supplier_id
    return f"{supplier.id} {supplier.name}"


def _price_comes_from(reason: str) -> str:
    match = re.search(r"Requirement value from ([^.]+)\.", reason)
    if not match:
        return ""
    source = match.group(1).strip()
    if source.startswith("PO "):
        return f"The price comes from purchase order {source[3:]}."
    if source == "item standard price":
        return "The price is the catalogue standard price."
    if source == "requester estimate":
        return "The price is the estimate you gave."
    if source.startswith("lowest applicable contract rate on "):
        contract = source.removeprefix("lowest applicable contract rate on ")
        return (
            f"The price is the lowest rate on contract {contract}. "
            "That contract has no room left for this quantity."
        )
    if source.startswith("RC-"):
        return f"The price comes from contract {source}."
    return f"The price comes from {source}."


def _decision_sentence(decision: Decision, catalog) -> str:
    route = decision.route
    plain = _ROUTE_PLAIN.get(route, route.replace("_", " ").lower() or "processed")
    lines = [f"Created {decision.pr_no}."]
    if route == "NOT_PROCESSED":
        reason = re.sub(r"\s*Policy [\d.]+\.\s*$", "", decision.reason).strip()
        lines.append(f"This was not processed. {reason}")
        if decision.policy_version:
            lines.append(f"Policy {decision.policy_version}.")
        return "\n".join(lines)

    clause = ""
    found = re.search(r"Clause (\S+)", decision.reason)
    if found:
        clause = found.group(1)
    lead = f"This is {plain} ({route})"
    if clause and decision.policy_version:
        lead += f", under clause {clause} of policy {decision.policy_version}"
    elif decision.policy_version:
        lead += f", under policy {decision.policy_version}"
    lead += "."
    lines.append(lead)
    why = _ROUTE_WHY.get(route)
    if why:
        lines.append(why)
    if decision.group_value_inr:
        lines.append(f"The requirement is worth ₹{int(float(decision.group_value_inr)):,}.")
    price = _price_comes_from(decision.reason)
    if price:
        lines.append(price)
    if decision.supplier_id:
        who = _supplier_label(catalog, decision.supplier_id)
        order = f"Purchase order {decision.po_id}" if decision.po_id else "The order"
        rate = f" at ₹{decision.unit_price} each" if decision.unit_price else ""
        lines.append(f"{order} goes to {who}{rate}.")
    contract = re.search(r"Contract (RC-[A-Za-z0-9-]+)", decision.reason)
    if contract:
        lines.append(f"It uses contract {contract.group(1)}.")
    if decision.suggested_supplier_ids:
        names = [
            _supplier_label(catalog, supplier_id.strip())
            for supplier_id in decision.suggested_supplier_ids.split(";")
            if supplier_id.strip()
        ]
        if names:
            lines.append("Suppliers to invite, best first:\n" + "\n".join(names))
    return "\n".join(lines)


def _original(row) -> str:
    return json.dumps({key: row[key] if key != "pr_line" else str(row[key]) for key in EXPORT_COLUMNS})


def _blocked(waiting: dict | None) -> bool:
    if waiting is None:
        return True
    return any(note.startswith("No effective date") for note in waiting.get("_notes", []))


app = create_app()
