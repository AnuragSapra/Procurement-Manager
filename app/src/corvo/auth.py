"""Named people, passphrases, and the upload checks around them.

The role lives on the server. A form can send a name, never a role.
Passphrases are the demo desk's. They are compared as hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import os

PEOPLE = (
    {
        "name": "A. Kulkarni",
        "role": "requester",
        "department": "Stores",
        "passphrase": "bhosari-stores",
    },
    {
        "name": "M. Shaikh",
        "role": "requester",
        "department": "Production",
        "passphrase": "chakan-line",
    },
    {
        "name": "R. Deshmukh",
        "role": "buyer",
        "department": "Procurement",
        "passphrase": "buyer-desk",
    },
)

PASSPHRASES = {person["name"]: person["passphrase"] for person in PEOPLE}
PUBLIC_PEOPLE = tuple(
    {key: person[key] for key in ("name", "role", "department")} for person in PEOPLE
)

CSV_LIMIT = 1_048_576
PDF_LIMIT = 2_097_152
TEXT_LIMIT = 1_048_576
SESSION_SECONDS = 8 * 60 * 60


def session_secret() -> str:
    secret = os.environ.get("CORVO_SECRET", "")
    env = os.environ.get("CORVO_ENV", "local")
    if env == "production" and secret in ("", "corvo-dev"):
        raise RuntimeError("Set CORVO_SECRET before starting outside local development.")
    return secret or "corvo-dev"


def identify(name: str, passphrase: str) -> dict | None:
    for person in PEOPLE:
        if person["name"] != name:
            continue
        if _same(person["passphrase"], passphrase):
            return {key: person[key] for key in ("name", "role", "department")}
        return None
    return None


def tokens_match(expected: str, given: str) -> bool:
    if len(expected) < 16 or len(expected) != len(given):
        return False
    return hmac.compare_digest(expected, given)


def check_csv(raw: bytes, filename: str = "") -> None:
    if len(raw) > CSV_LIMIT:
        raise ValueError("CSV is larger than 1 MB.")
    if filename and not filename.lower().endswith(".csv"):
        raise ValueError("Upload a .csv file.")
    if b"\x00" in raw:
        raise ValueError("Upload a .csv file.")


def check_pdf(raw: bytes, filename: str) -> None:
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Upload a PDF.")
    if len(raw) > PDF_LIMIT:
        raise ValueError("PDF is larger than 2 MB.")
    if not raw.startswith(b"%PDF"):
        raise ValueError("That file is not a PDF.")


def check_text(raw: bytes) -> None:
    if len(raw) > TEXT_LIMIT:
        raise ValueError("That file is larger than 1 MB.")


def _same(expected: str, given: str) -> bool:
    left = hashlib.pbkdf2_hmac("sha256", expected.encode(), b"corvo-person", 120_000)
    right = hashlib.pbkdf2_hmac("sha256", given.encode(), b"corvo-person", 120_000)
    return hmac.compare_digest(left, right)
