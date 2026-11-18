from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from corvo.language import intent_of
from corvo.load import UOMS, unit_label
from corvo.match import Matcher, _edit_distance, tokens
from corvo.models import Catalog, PRLine

_FILLER = re.compile(
    r"\b(please|i|need|want|order|buy|raise|get|a|an|the|of|for|me|some|pr|suppose|say|like|around|approximately|approx|at|on|by|in|from|with|to|and)\b",
    re.I,
)
_UOM = "|".join(UOMS)


@dataclass
class ChatTurn:
    draft: dict
    reply: str
    line: PRLine | None = None
    reset_transcript: bool = False


def fresh_draft() -> dict:
    return {"awaiting": None, "urgency_word": False, "plant_ambiguous": False}


def next_friday(today: date) -> date:
    days = (4 - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


_YES = {"yes", "y", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm", "create", "go ahead", "do it"}
_NO = {"no", "n", "nope", "nah", "cancel", "stop"}
_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_LEFTOVER = {
    "it", "its", "it's", "this", "that", "please", "on", "by", "for", "me",
    "need", "want", "and", "to", "the", "a", "an", "all", "is", "was",
}
_GENERIC = {
    "item", "items", "thing", "things", "stuff", "product", "products",
    "material", "materials", "good", "goods", "something", "anything",
}
_LEAD = re.compile(
    r"^(?:h+e+y+|h+i+|hello+|hiya|heya|namaste|good\s+(?:morning|afternoon|evening))(?:\s+there)?[,!\s]+",
    re.I,
)
_PLANT_WORDS = {
    "pun1": "PUN1",
    "pun": "PUN1",
    "bhosari": "PUN1",
    "bhosri": "PUN1",
    "chk1": "CHK1",
    "chk": "CHK1",
    "chkn": "CHK1",
    "chakan": "CHK1",
    "chakn": "CHK1",
}
_PLANT_LABEL = {"PUN1": "Bhosari", "CHK1": "Chakan"}
_FUZZY_PLANTS = (
    ("bhosari", "PUN1"),
    ("chakan", "CHK1"),
    ("pun1", "PUN1"),
    ("chk1", "CHK1"),
)
_ANSWERING = {"plant", "need_by", "quantity", "estimate", "urgency", "confirm"}
_MONTH_SHORT = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_RELATIVE_DAY = (
    r"tomorrow|tommorow|tommorrow|tomorow|tomarrow|tomorro|tmrw|tmr|tmw|2morrow|2moro"
)


def turn(
    text: str,
    draft: dict,
    catalog: Catalog,
    today: date,
    requester: str,
    department: str,
    now: datetime,
) -> ChatTurn:
    draft = {**fresh_draft(), **draft}
    cleaned = text.strip()
    if not cleaned:
        return ChatTurn(draft, "Tell me the item, the quantity, the plant, and the need-by date.")
    word = re.sub(r"[!?.]+$", "", cleaned).strip().lower()

    intent = intent_of(cleaned)
    buying = _requisition_signal(cleaned, catalog, today)
    only_slot = _slot_signal(cleaned, today) and not _content_left(cleaned, today)
    only_number = bool(re.fullmatch(r"[\d,]+(?:\.\d+)?", word))
    answering = draft.get("awaiting") in {"confirm", "urgency", "suggest", "plant"} and bool(
        _leading_answer(cleaned)
    )
    if (
        intent == "clear"
        and "<" not in cleaned
        and not _quantity(cleaned, None)[0]
        and not answering
        and not only_slot
    ):
        return ChatTurn(fresh_draft(), "", reset_transcript=True)
    if not buying and not answering and not only_slot and not only_number:
        if intent == "cancel":
            return ChatTurn(fresh_draft(), "Cancelled. What do you need to buy?")
        if intent in {"greet", "greet_morning", "greet_afternoon", "greet_evening"}:
            return ChatTurn(fresh_draft(), _intent_reply(intent, cleaned, requester, draft))
        if intent in {"converse", "help", "thanks", "bye", "sorry", "wellbeing"}:
            return ChatTurn(draft, _intent_reply(intent, cleaned, requester, draft))

    if draft.get("awaiting") == "urgency":
        answer = _leading_answer(cleaned)
        said_urgent = bool(re.search(r"\b(urgent|emergency)\b", cleaned, re.I))
        if answer == "yes" or said_urgent or word in {"emergency", "urgent"}:
            draft["urgency"] = "Emergency"
        elif answer == "no" or word == "normal" or re.search(r"\bnormal\b", cleaned, re.I):
            draft["urgency"] = "Normal"
        else:
            return ChatTurn(draft, "Should I record this as Emergency? Yes or no." if draft.get("urgency_word") else "Is this urgent?")
        draft["urgency_word"] = False
        draft["awaiting"] = None
        rest = _without_leading_answer(cleaned) if answer else cleaned
        rest = re.sub(r"\b(?:it'?s|it is|this is)\s+(?:urgent|emergency|normal)\b", " ", rest, flags=re.I)
        rest = re.sub(r"\b(?:urgent|emergency|normal)\b", " ", rest, flags=re.I)
        if not _plain(rest):
            return _ask(draft, catalog)
        cleaned = rest.strip()
        draft["_bundle"] = True

    if draft.get("awaiting") == "estimate" and not draft.get("declined_suggestion"):
        recovered = _catalogue_phrase(draft.get("description"))
        if recovered and recovered != (draft.get("description") or ""):
            kind, items = Matcher(catalog).interpret(recovered)
            if kind != "none":
                draft["description"] = recovered
                draft["matched"] = False
                draft.pop("item_code", None)
                draft.pop("estimate", None)
                if kind == "suggest":
                    draft["suggestions"] = [
                        {"code": item.code, "description": item.description} for item in items
                    ]
                    draft["awaiting"] = "suggest"
                else:
                    item = items[0]
                    draft["item_code"] = item.code
                    draft["description"] = item.description
                    draft["uom"] = item.uom
                    draft["matched"] = True
                    draft.pop("suggestions", None)
                    draft["awaiting"] = None
                if re.fullmatch(r"(?:(?:rs\.?|inr|₹)\s*)?[\d,]+(?:\.\d+)?", cleaned.strip(), re.I):
                    return _ask(draft, catalog)

    if draft.get("awaiting") == "suggest":
        suggestions = draft.get("suggestions") or []
        answer = _leading_answer(cleaned)
        chosen = _chosen_suggestion(_plain(cleaned), suggestions)
        if chosen is None and len(suggestions) == 1 and answer != "no":
            if answer == "yes" or _looks_like_details(cleaned):
                chosen = suggestions[0]
        if chosen:
            item = catalog.items[chosen["code"]]
            draft["item_code"] = item.code
            draft["description"] = item.description
            draft["uom"] = item.uom
            draft["matched"] = True
            draft.pop("suggestions", None)
            draft["awaiting"] = None
            if answer:
                cleaned = _without_leading_answer(cleaned)
            if not _plain(cleaned):
                return _ask(draft, catalog)
            draft["_bundle"] = True
        elif answer == "no" and len(suggestions) == 1:
            draft.pop("suggestions", None)
            draft.pop("item_code", None)
            draft["matched"] = False
            draft["declined_suggestion"] = True
            draft["awaiting"] = None
            cleaned = _without_leading_answer(cleaned)
            if not _plain(cleaned):
                return _ask(draft, catalog)
        else:
            draft.pop("suggestions", None)
            draft["awaiting"] = None

    if draft.get("awaiting") == "plant" and draft.get("plant_suggestion"):
        answer = _leading_answer(cleaned)
        if answer == "yes":
            code = draft.pop("plant_suggestion")
            draft["plant"] = code
            draft["plant_ambiguous"] = False
            draft["awaiting"] = None
            cleaned = _without_leading_answer(cleaned)
            if not _plain(cleaned):
                draft["plant_note"] = f"I'll use {code} ({_PLANT_LABEL[code]})."
                return _ask(draft, catalog)
            draft["_bundle"] = True
        elif answer == "no" and not _plain(_without_leading_answer(cleaned)):
            draft.pop("plant_suggestion", None)
            draft["awaiting"] = "plant"
            return ChatTurn(draft, "Which plant: Bhosari (PUN1) or Chakan (CHK1)?")
        else:
            draft.pop("plant_suggestion", None)

    if draft.get("awaiting") == "confirm":
        answer = _leading_answer(cleaned)
        rest = _without_leading_answer(cleaned) if answer else cleaned
        if word in _YES or (answer == "yes" and _only_polite(rest)):
            need = date.fromisoformat(draft["need_by"])
            if need < today:
                draft["awaiting"] = "need_by"
                draft.pop("need_by", None)
                return ChatTurn(draft, _past_date(need))
            line = _line(draft, requester, department, now)
            return ChatTurn(fresh_draft(), "Created the PR.", line)
        if word in _NO or (answer == "no" and _only_polite(rest)):
            return ChatTurn(fresh_draft(), "Cancelled. What do you need to buy?")
        return ChatTurn(draft, "Say yes to create the PR, or no to cancel.")

    if _wants_catalogue(cleaned):
        return ChatTurn(draft, _catalogue(catalog))

    if draft.get("awaiting") == "estimate" and _junk_description(draft.get("description")):
        draft.pop("description", None)
        draft.pop("item_code", None)
        draft["matched"] = False
        draft["awaiting"] = None
        if re.fullmatch(r"(?:(?:rs\.?|inr|₹)\s*)?[\d,]+(?:\.\d+)?", cleaned.strip(), re.I):
            draft["_recovered"] = True
            return _ask(draft, catalog)

    _absorb(_LEAD.sub("", cleaned), draft, catalog, today)
    if draft.pop("_bundle", None):
        note = _bundle_note(draft)
        if note:
            draft["bundle_note"] = note
    if draft.pop("_chatter", None):
        return ChatTurn(
            draft,
            "I didn't take that as an item. Tell me what you need, or say help for the kind of message I understand.",
        )
    return _ask(draft, catalog)


_POLITE = {
    "please", "pls", "thanks", "thank", "you", "kindly", "now", "it", "the", "pr",
    "go", "ahead", "do", "create",
}


def _only_polite(text: str) -> bool:
    if re.search(r"\d", text):
        return False
    words = re.findall(r"[A-Za-z']+", text)
    allowed = _NOISE | _POLITE | _YES | _NO
    return all(word.lower() in allowed for word in words)


def _plain(text: str) -> str:
    low = re.sub(r"[!?.]+", "", text).strip().lower()
    return re.sub(r"\s+", " ", low)


def _leading_answer(text: str) -> str | None:
    low = _plain(text)
    if re.match(r"^(?:yes|yeah|yep|yup|y|ok|okay|sure|confirm|go ahead|do it)\b", low):
        return "yes"
    if re.match(r"^(?:no|nope|nah|n)\b", low):
        return "no"
    return None


def _without_leading_answer(text: str) -> str:
    return re.sub(
        r"^\s*(?:yes|yeah|yep|yup|y|ok|okay|sure|confirm|go ahead|do it|no|nope|nah|n)\b[\s,.;:!?-]*",
        "",
        text,
        count=1,
        flags=re.I,
    ).strip()


_NOISE = _LEFTOVER | _GENERIC | {
    "yes", "yeah", "yep", "yup", "no", "nope", "nah", "ok", "okay", "sure",
    "its", "it's", "urgent", "emergency", "tomorrow", "today", "asap", "tmrw", "tmr",
    "pun", "chk", "pun1", "chk1", "bhosari", "chakan", "pune", "qty", "quantity",
}


def _junk_description(text: str | None) -> bool:
    if not text or not str(text).strip():
        return False
    words = re.findall(r"[A-Za-z]+", str(text))
    return not any(word.lower() not in _NOISE for word in words)


def _looks_like_details(text: str) -> bool:
    if re.search(r"\b(?:tomorrow|today|yesterday|asap|tmrw|tmr|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, re.I):
        return True
    if re.search(r"\b\d{1,2}[./-]\d{1,2}\b", text):
        return True
    if re.search(r"\b(?:urgent|emergency)\b", text, re.I):
        return True
    if re.search(r"\b(?:pun1?|chk1?|bhosari|chakan|pune)\b", text, re.I):
        return True
    return bool(re.search(r"\b\d+(?:\.\d+)?\b", text))


def _recover_text(draft: dict) -> str:
    bits = []
    if draft.get("quantity"):
        bits.append(f"quantity {draft['quantity']}")
    if draft.get("plant") in _PLANT_LABEL:
        bits.append(f"{draft['plant']} ({_PLANT_LABEL[draft['plant']]})")
    if draft.get("need_by"):
        bits.append(f"need by {_spoken_date(date.fromisoformat(draft['need_by']))}")
    if draft.get("urgency") == "Emergency" or draft.get("urgency_word"):
        bits.append("urgent")
    kept = f" I'm still holding {', '.join(bits)}." if bits else ""
    return "That wasn't an item name, so I didn't price it." + kept + " What do you need to buy?"


def _bundle_note(draft: dict) -> str:
    parts = []
    if draft.get("description"):
        parts.append(str(draft["description"]))
    if draft.get("quantity"):
        parts.append(f"quantity {draft['quantity']}")
    if draft.get("plant") in _PLANT_LABEL:
        code = draft["plant"]
        parts.append(f"{code} ({_PLANT_LABEL[code]})")
    if draft.get("need_by"):
        parts.append(f"need by {_spoken_date(date.fromisoformat(draft['need_by']))}")
    if draft.get("urgency") == "Emergency" or draft.get("urgency_word"):
        parts.append("urgent")
    if len(parts) < 2:
        return ""
    return "Noted: " + ", ".join(parts) + "."


def _draft_has_progress(draft: dict) -> bool:
    return any(
        draft.get(key)
        for key in ("description", "item_code", "quantity", "plant", "need_by", "estimate", "suggestions")
    )


def _content_left(text: str, today: date) -> str:
    phrase = _catalogue_phrase(text)
    _need, raw_date = _need_by(text, today)
    if raw_date:
        phrase = re.sub(re.escape(raw_date), " ", phrase, count=1, flags=re.I)
    phrase = re.sub(
        r"\b(?:bhosari|bhosri|chakan|chakn|chkn|pun1|chk1|pune|pun|chk)\b",
        " ",
        phrase,
        flags=re.I,
    )
    phrase = re.sub(r"\b\d+(?:\.\d+)?\b", " ", phrase)
    return re.sub(r"\s+", " ", phrase).strip()


def _requisition_signal(text: str, catalog: Catalog, today: date) -> bool:
    phrase = _content_left(text, today)
    if phrase and Matcher(catalog).interpret(phrase)[0] != "none":
        return True
    return bool(_quantity(text, None)[0] and phrase)


def _slot_signal(text: str, today: date) -> bool:
    plant, ambiguous, unknown, suggestion, _span = _plant(text)
    need, _raw = _need_by(text, today)
    return bool(plant or ambiguous or unknown or suggestion or need)


def _intent_reply(intent: str, text: str, requester: str, draft: dict) -> str:
    if intent == "help":
        return (
            "Tell me the item, the quantity, the plant, and when you need it. "
            "A date can be tomorrow, next Friday, 20 Nov 2026, or 20/11/2026. "
            "CHK or Chakan is CHK1. PUN or Bhosari is PUN1. "
            "Say list items to see everything you can request. "
            "Say clear the chat to wipe this conversation. I ask before anything is created."
        )
    if intent == "thanks":
        return "You're welcome. Tell me whenever you need to raise another requisition."
    if intent == "bye":
        return "See you. Nothing is ordered until you confirm a requisition."
    if intent == "sorry":
        return "No problem. Tell me the item, the quantity, the plant, and the date whenever you're ready."
    if intent == "wellbeing":
        if _draft_has_progress(draft):
            return f"I'm doing well, {requester}. We can stay with this requisition."
        return f"I'm doing well, {requester}. What do you need to buy?"
    if intent == "greet_morning":
        return f"Good morning, {requester}. What do you need to buy?"
    if intent == "greet_afternoon":
        return f"Good afternoon, {requester}. What do you need to buy?"
    if intent == "greet_evening":
        return f"Good evening, {requester}. What do you need to buy?"
    if intent == "greet":
        return (
            f"Hello, {requester}. I can raise a requisition when you're ready. "
            "Tell me what you need, how many, which plant, and when."
        )
    if _draft_has_progress(draft):
        return "Alright. We can stay with this requisition, or say clear the chat to wipe the conversation."
    return "I'm here when you want to raise a requisition. What do you need to buy?"


def looks_like_policy(text: str) -> bool:
    low = text.lower()
    if re.search(r"\b(policy|amendment)\b", low):
        return True
    if re.search(r"\b(spot|emergency|rfq|rfp)\b", low) and re.search(r"\b(limit|ceiling|threshold)\b", low):
        return True
    if re.search(r"\bif\s+plant\b", low) and re.search(r"\bthen\b", low):
        return True
    return bool(re.search(r"\b(clubbing|need-by) window\b", low))


def _generic_request(phrase: str) -> bool:
    words = [word for word in re.findall(r"[A-Za-z]+", phrase) if word.lower() not in _LEFTOVER]
    return bool(words) and all(word.lower() in _GENERIC for word in words)


def _wants_catalogue(text: str) -> bool:
    low = re.sub(r"[?!.]+", "", text).strip().lower()
    low = re.sub(r"\s+", " ", low)
    if re.search(r"\b(catalogue|catalog)\b", low):
        return True
    if re.search(r"\b(list|show|display)\b", low) and re.search(r"\bitems?\b", low):
        return True
    if re.search(r"\b(all the items|every item|item list|list of items)\b", low):
        return True
    return bool(
        re.search(r"\b(what|which)\b", low)
        and re.search(r"\bitems?\b", low)
        and re.search(r"\b(request|order|buy|available|have)\b", low)
    )


def _catalogue(catalog: Catalog) -> str:
    lines = [
        f"{item.code} {item.description}"
        for item in sorted(catalog.items.values(), key=lambda item: item.code)
    ]
    return "These are the items you can request:\n" + "\n".join(lines)


def _absorb(text: str, draft: dict, catalog: Catalog, today: date) -> None:
    if draft.get("awaiting") == "estimate":
        amount = re.search(r"([\d,]+(?:\.\d+)?)", text)
        if amount:
            draft["estimate"] = amount.group(1).replace(",", "")
            text = text[: amount.start()] + " " + text[amount.end() :]

    work = text
    need, raw_date = _need_by(text, today)
    if need:
        if need < today:
            draft["past_date"] = need.isoformat()
            draft.pop("need_by", None)
        else:
            draft.pop("past_date", None)
            draft["need_by"] = need.isoformat()
            if raw_date and _message_is_only(text, raw_date):
                draft["date_note"] = f"I'll use {_spoken_date(need)}."
        work = work.replace(raw_date, " ", 1)
    elif draft.get("awaiting") == "need_by":
        draft["date_miss"] = True

    plant, ambiguous, unknown, suggestion, span = _plant(
        work, suggest=draft.get("awaiting") == "plant"
    )
    if unknown:
        draft["unknown_plant"] = unknown
        draft.pop("plant", None)
        draft.pop("plant_suggestion", None)
        draft["plant_ambiguous"] = False
    elif plant:
        draft["plant"] = plant
        draft["plant_ambiguous"] = False
        draft.pop("unknown_plant", None)
        draft.pop("plant_suggestion", None)
        if span and _message_is_only(text, span):
            draft["plant_note"] = f"I'll use {plant} ({_PLANT_LABEL[plant]})."
    elif suggestion:
        draft["plant_suggestion"] = suggestion
        draft.pop("plant", None)
        draft.pop("unknown_plant", None)
        draft["plant_ambiguous"] = False
    elif ambiguous:
        draft["plant_ambiguous"] = True
        draft.pop("unknown_plant", None)
        draft.pop("plant_suggestion", None)
    elif draft.get("awaiting") == "plant":
        draft["plant_miss"] = True
    if span and suggestion:
        work = re.sub(rf"\b{re.escape(span)}\b", " ", work, count=1, flags=re.I)
    work = re.sub(r"\b[A-Za-z]{3}\d+\b", " ", work)
    work = re.sub(
        r"\b(bhosari|bhosri|chakan|chakn|chkn|pun1|chk1|pune|pun|chk)\b",
        " ",
        work,
        flags=re.I,
    )

    if re.search(r"\b(?:it'?s|it is|this is|and|make it|mark it)\s+(?:an?\s+)?(?:urgent|emergency)\b", text, re.I):
        draft["urgency"] = "Emergency"
        draft["urgency_word"] = False
    elif re.search(r"\b(urgent|emergency)\b", text, re.I):
        draft["urgency_word"] = True
        draft.pop("urgency", None)
    work = re.sub(r"\b(?:it'?s|it is|this is|and|make it|mark it)\s+(?:an?\s+)?(?:urgent|emergency)\b", " ", work, flags=re.I)
    work = re.sub(r"\b(urgent|emergency)\b", " ", work, flags=re.I)

    estimate = re.search(
        r"(?:estimate|estimated|about|₹|rs\.?|inr)\s*₹?\s*([\d,]+(?:\.\d+)?)",
        work,
        re.I,
    )
    if estimate is None:
        estimate = re.search(r"\bat\s+₹?\s*([\d,]+(?:\.\d+)?)", work, re.I)
    if estimate:
        draft["estimate"] = estimate.group(1).replace(",", "")
        work = work[: estimate.start()] + " " + work[estimate.end() :]

    quantity, uom = _quantity(work, draft.get("awaiting"), draft.get("description") or "")
    if quantity:
        draft["quantity"] = quantity
        if uom:
            draft["stated_uom"] = uom
        work = _blank_quantity(work)
        work = re.sub(rf"\b{re.escape(quantity)}\b", " ", work, count=1)

    code = re.search(r"ITM-\d+", text, re.I)
    if code and code.group(0).upper() in catalog.items:
        item = catalog.items[code.group(0).upper()]
        draft["item_code"] = item.code
        draft["description"] = draft.get("description") or item.description
        draft["uom"] = item.uom
        draft["matched"] = True
        return

    phrase = _FILLER.sub(" ", work)
    phrase = re.sub(r"\s+", " ", phrase).strip(" .,-")
    if _generic_request(phrase):
        number = re.search(r"\b(\d+(?:\.\d+)?)\b", phrase)
        if number and not draft.get("quantity"):
            draft["quantity"] = number.group(1)
        draft.pop("description", None)
        draft.pop("item_code", None)
        draft.pop("choices", None)
        draft["matched"] = False
        draft["ask_kind"] = True
        return
    if not phrase and draft.get("awaiting") != "item":
        return
    if not phrase:
        return
    matcher = Matcher(catalog)
    kind, items = matcher.interpret(phrase)
    if kind == "exact":
        item = items[0]
        draft["item_code"] = item.code
        draft["description"] = item.description
        draft["uom"] = item.uom
        draft["matched"] = True
        draft.pop("suggestions", None)
        draft.pop("declined_suggestion", None)
    elif kind == "suggest":
        if draft.get("item_code") and draft.get("matched"):
            return
        if _junk_description(phrase):
            return
        draft.pop("declined_suggestion", None)
        draft["suggestions"] = [{"code": item.code, "description": item.description} for item in items]
        draft["description"] = phrase
        draft.pop("item_code", None)
        draft["matched"] = False
    else:
        words = [word for word in phrase.split() if word.lower() not in _LEFTOVER and not _junk_description(word)]
        if not words:
            return
        if draft.get("awaiting") in _ANSWERING:
            return
        if draft.get("item_code") and draft.get("matched"):
            return
        purchasing = any(draft.get(key) for key in ("quantity", "plant", "need_by", "estimate"))
        if not purchasing and intent_of(" ".join(words)) in {
            "converse", "help", "greet", "greet_morning", "greet_afternoon",
            "greet_evening", "thanks", "bye", "sorry", "wellbeing", "clear",
        }:
            draft["_chatter"] = True
            return
        if draft.get("awaiting") == "item" or len(words) >= 2 or purchasing:
            draft["description"] = " ".join(words)
            draft.pop("item_code", None)
            draft["matched"] = False
            draft["uom"] = draft.get("stated_uom") or "NOS"
        else:
            draft.pop("description", None)
            draft.pop("item_code", None)
            draft["_chatter"] = True


def _ask(draft: dict, catalog: Catalog) -> ChatTurn:
    result = _question(draft, catalog)
    result.reply = _heard(result.draft, result.reply)
    return result


def _heard(draft: dict, reply: str) -> str:
    notes = []
    for key in ("bundle_note", "plant_note", "date_note"):
        note = draft.pop(key, None)
        if note:
            notes.append(note)
    if not notes:
        return reply
    return "\n".join(notes + [reply])


def _question(draft: dict, catalog: Catalog) -> ChatTurn:
    if draft.pop("_recovered", None) and not draft.get("item_code") and not draft.get("description"):
        draft["awaiting"] = "item"
        return ChatTurn(draft, _recover_text(draft))
    if draft.get("unknown_plant"):
        code = draft.pop("unknown_plant")
        draft["awaiting"] = "plant"
        return ChatTurn(
            draft,
            f"No such plant as {code}. We only serve two plants, PUN1 (Bhosari) and CHK1 (Chakan).",
        )
    if draft.get("past_date"):
        when = draft.pop("past_date")
        draft.pop("need_by", None)
        draft["awaiting"] = "need_by"
        return ChatTurn(draft, _past_date(date.fromisoformat(when)))
    if not draft.get("description") and not draft.get("item_code"):
        draft["awaiting"] = "item"
        if draft.get("quantity") or draft.pop("ask_kind", None):
            return ChatTurn(draft, "What kind of item?")
        return ChatTurn(draft, "What do you need to buy?")
    if draft.get("suggestions") and not draft.get("item_code"):
        draft["awaiting"] = "suggest"
        if len(draft["suggestions"]) == 1:
            name = draft["suggestions"][0]["description"]
            return ChatTurn(draft, f'Did you mean "{name}"?')
        listed = "\n".join(f'{item["code"]} {item["description"]}' for item in draft["suggestions"])
        return ChatTurn(draft, f"Which of these did you mean?\n{listed}")
    if not draft.get("quantity"):
        draft["awaiting"] = "quantity"
        return ChatTurn(draft, "How many?")
    if draft.get("plant_suggestion") and not draft.get("plant"):
        draft["awaiting"] = "plant"
        code = draft["plant_suggestion"]
        return ChatTurn(draft, f"Did you mean {code} ({_PLANT_LABEL[code]})?")
    if draft.get("plant_ambiguous") or not draft.get("plant"):
        draft["awaiting"] = "plant"
        if draft.get("plant_ambiguous"):
            return ChatTurn(
                draft,
                "Pune has two plants. Do you mean Bhosari (PUN1) or Chakan (CHK1)?",
            )
        if draft.pop("plant_miss", None):
            return ChatTurn(
                draft,
                "I didn't catch the plant. Which plant: Bhosari (PUN1) or Chakan (CHK1)? CHK or PUN is enough.",
            )
        return ChatTurn(draft, "Which plant: Bhosari (PUN1) or Chakan (CHK1)? CHK or PUN is enough.")
    if not draft.get("need_by"):
        draft["awaiting"] = "need_by"
        if draft.pop("date_miss", None):
            return ChatTurn(
                draft,
                "I didn't catch a date. You can say tomorrow, next Friday, or 20/11/2026.",
            )
        return ChatTurn(draft, "When do you need it? Tomorrow, next Friday, or a date.")
    if not draft.get("item_code") and not draft.get("estimate"):
        if _junk_description(draft.get("description")):
            draft.pop("description", None)
            draft["matched"] = False
            draft["awaiting"] = "item"
            return ChatTurn(draft, _recover_text(draft))
        if draft.get("description") and not draft.get("suggestions") and not draft.get("declined_suggestion"):
            kind, items = Matcher(catalog).interpret(draft["description"])
            if kind == "exact" and items:
                item = items[0]
                draft["item_code"] = item.code
                draft["description"] = item.description
                draft["uom"] = item.uom
                draft["matched"] = True
            elif kind == "suggest" and items:
                draft["suggestions"] = [{"code": item.code, "description": item.description} for item in items]
                draft["awaiting"] = "suggest"
                if len(items) == 1:
                    return ChatTurn(draft, f'Did you mean "{items[0].description}"?')
                listed = "; ".join(f"{item.code} {item.description}" for item in items)
                return ChatTurn(draft, f"Which item did you mean? {listed}")
        if not draft.get("item_code"):
            draft["awaiting"] = "estimate"
            return ChatTurn(
                draft,
                "That is not in the item master, so it will be raised as a new item. What is the estimated unit price?",
            )
    if draft.get("urgency_word") and draft.get("urgency") not in {"Emergency", "Normal"}:
        draft["awaiting"] = "urgency"
        return ChatTurn(draft, "You said urgent. Should I record this as Emergency? Yes or no.")
    if not draft.get("urgency"):
        draft["awaiting"] = "urgency"
        return ChatTurn(draft, "Is this urgent?")
    draft["awaiting"] = "confirm"
    return ChatTurn(draft, _summary(draft, catalog))


def _summary(draft: dict, catalog: Catalog) -> str:
    code = draft.get("item_code") or "new item"
    name = draft.get("description") or ""
    if code in catalog.items:
        name = catalog.items[code].description
    plant = draft["plant"]
    plant_name = "Bhosari" if plant == "PUN1" else "Chakan"
    lines = [
        "Create this PR?",
        f"{draft['quantity']} {unit_label(draft.get('uom') or 'NOS')} {name} ({code})",
        f"Plant {plant} ({plant_name})",
        f"Need by {_spoken_date(date.fromisoformat(draft['need_by']))}",
        f"Urgency {draft['urgency']}",
    ]
    if draft.get("estimate"):
        lines.append(f"Estimate ₹{draft['estimate']}")
    lines.append("Yes to create it, or no to cancel.")
    return "\n".join(lines)


def _line(draft: dict, requester: str, department: str, now: datetime) -> PRLine:
    created = now.replace(second=0, microsecond=0)
    estimate = Decimal(draft["estimate"]) if draft.get("estimate") else None
    return PRLine(
        pr_no="",
        pr_line=1,
        created_at=created,
        requester=requester,
        department=department,
        plant=draft["plant"],
        item_code=draft.get("item_code") or "",
        description=draft.get("description") or "",
        quantity=Decimal(str(draft["quantity"])),
        uom=draft.get("uom") or "NOS",
        estimate=estimate,
        need_by=date.fromisoformat(draft["need_by"]),
        urgency=draft.get("urgency") or "Normal",
    )


def _catalogue_phrase(text: str | None) -> str:
    phrase = _FILLER.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", phrase).strip(" .,-")


def _chosen_suggestion(word: str, suggestions: list[dict]) -> dict | None:
    if len(suggestions) == 1 and word in _YES:
        return suggestions[0]
    for item in suggestions:
        if item["code"].lower() in word or item["description"].lower() in word:
            return item
    ordinal = re.fullmatch(r"(?:the\s+)?(?:1|first|1st|2|second|2nd|3|third|3rd)(?:\s+one)?", word)
    if ordinal and suggestions:
        label = re.search(r"1|first|1st|2|second|2nd|3|third|3rd", word)
        index = {"1": 0, "first": 0, "1st": 0, "2": 1, "second": 1, "2nd": 1, "3": 2, "third": 2, "3rd": 2}[
            label.group(0)
        ]
        if index < len(suggestions):
            return suggestions[index]
    typed = set(tokens(word))
    hits = []
    for item in suggestions:
        if typed & set(tokens(item["description"])):
            hits.append(item)
    if len(hits) == 1:
        return hits[0]
    return None


def _past_date(when: date) -> str:
    return (
        f"{when.isoformat()} is in the past. "
        "Give a need-by date of today or later."
    )


def _plant(
    text: str, suggest: bool = False
) -> tuple[str | None, bool, str | None, str | None, str]:
    codes = [match.group(1).upper() for match in re.finditer(r"\b([A-Za-z]{3}\d+)\b", text)]
    unknown = [code for code in codes if code not in {"PUN1", "CHK1"}]
    if unknown:
        return None, False, unknown[0], None, ""
    found: list[tuple[str, str]] = []
    for match in re.finditer(r"\b(PUN1|CHK1)\b", text, re.I):
        found.append((match.group(1).upper(), match.group(1)))
    for match in re.finditer(r"\b([A-Za-z]+)\b", text):
        word = match.group(1)
        plant = _PLANT_WORDS.get(word.lower())
        if plant and plant not in {item[0] for item in found}:
            found.append((plant, word))
    if len(found) == 1:
        return found[0][0], False, None, None, found[0][1]
    if len(found) > 1:
        return None, True, None, None, ""
    if re.search(r"\bpune\b", text, re.I):
        return None, True, None, None, ""
    if suggest:
        guesses: list[tuple[str, str]] = []
        for match in re.finditer(r"\b([A-Za-z]{3,})\b", text):
            word = match.group(1)
            plant = _close_plant(word.lower())
            if plant and plant not in {item[0] for item in guesses}:
                guesses.append((plant, word))
        if len(guesses) == 1:
            return None, False, None, guesses[0][0], guesses[0][1]
    return None, False, None, None, ""


def _close_plant(word: str) -> str | None:
    if word in _PLANT_WORDS or word == "pune":
        return None
    limit = 2 if len(word) >= 6 else 1 if len(word) >= 4 else 0
    if limit == 0:
        return None
    best: str | None = None
    best_distance = limit + 1
    for target, plant in _FUZZY_PLANTS:
        distance = _edit_distance(word, target)
        if distance <= limit and distance < best_distance:
            best = plant
            best_distance = distance
    return best


def _message_is_only(text: str, span: str) -> bool:
    rest = re.sub(re.escape(span), " ", text, count=1, flags=re.I)
    rest = _FILLER.sub(" ", rest)
    rest = re.sub(r"\b(plant|at|on|by|date|need|it|this|that)\b", " ", rest, flags=re.I)
    return re.search(r"[A-Za-z0-9]", rest) is None


def _spoken_date(when: date) -> str:
    return f"{when.day} {_MONTH_SHORT[when.month - 1]} {when.year}"


def _need_by(text: str, today: date) -> tuple[date | None, str]:
    found = _find_date(text, today)
    if found is None:
        return None, ""
    return found


def _find_date(text: str, today: date) -> tuple[date, str] | None:
    year_first = re.search(r"\b(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\b", text)
    if year_first:
        parsed = _safe_date(int(year_first.group(1)), int(year_first.group(2)), int(year_first.group(3)))
        if parsed:
            return parsed, year_first.group(0)

    numeric = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", text)
    if numeric:
        parsed = _numeric_date(int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3)))
        if parsed:
            return parsed, numeric.group(0)

    for month_first in re.finditer(
        r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)(?:\s*,?\s*(20\d{2}))?\b",
        text,
    ):
        if not _MONTHS.get(month_first.group(1).lower()[:3]):
            continue
        parsed = _named_month(int(month_first.group(2)), month_first.group(1), month_first.group(3), today)
        if parsed:
            return parsed, month_first.group(0)

    for day_first in re.finditer(
        r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([A-Za-z]+)(?:\s*,?\s*(20\d{2}))?\b",
        text,
    ):
        if not _MONTHS.get(day_first.group(2).lower()[:3]):
            continue
        parsed = _named_month(int(day_first.group(1)), day_first.group(2), day_first.group(3), today)
        if parsed:
            return parsed, day_first.group(0)

    later = re.search(rf"\bday\s+after\s+(?:{_RELATIVE_DAY})\b", text, re.I)
    if later:
        return today + timedelta(days=2), later.group(0)
    tomorrow = re.search(rf"\b(?:{_RELATIVE_DAY})\b", text, re.I)
    if tomorrow:
        return today + timedelta(days=1), tomorrow.group(0)
    asap = re.search(r"\b(?:asap|as soon as possible)\b", text, re.I)
    if asap:
        return today, asap.group(0)
    fortnight = re.search(r"\b(?:in\s+a\s+fortnight|in\s+two\s+weeks|in\s+2\s+weeks)\b", text, re.I)
    if fortnight:
        return today + timedelta(days=14), fortnight.group(0)
    weeks = re.search(r"\bin\s+(\d+)\s+weeks?\b", text, re.I)
    if weeks:
        return today + timedelta(days=7 * int(weeks.group(1))), weeks.group(0)
    month_end = re.search(r"\b(?:end of (?:the )?month|month end)\b", text, re.I)
    if month_end:
        return _end_of_month(today), month_end.group(0)
    week_end = re.search(r"\b(?:end of (?:the )?week|weekend)\b", text, re.I)
    if week_end:
        weekday = 5 if week_end.group(0).lower() == "weekend" else 4
        return _weekday_on(today, weekday, False), week_end.group(0)
    next_month = re.search(r"\bnext\s+month\b", text, re.I)
    if next_month:
        return _shift_month(today, 1), next_month.group(0)
    yesterday = re.search(r"\byesterday\b", text, re.I)
    if yesterday:
        return today - timedelta(days=1), yesterday.group(0)
    today_word = re.search(r"\btoday\b", text, re.I)
    if today_word:
        return today, today_word.group(0)
    week = re.search(r"\bnext\s+week\b|\bin\s+a\s+week\b", text, re.I)
    if week:
        return today + timedelta(days=7), week.group(0)
    ahead = re.search(r"\bin\s+(\d+)\s+days?\b", text, re.I)
    if ahead:
        return today + timedelta(days=int(ahead.group(1))), ahead.group(0)

    last_day = re.search(
        r"\blast\s+(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)\b",
        text,
        re.I,
    )
    if last_day:
        target = _WEEKDAYS[last_day.group(1).lower()]
        delta = (today.weekday() - target) % 7
        if delta == 0:
            delta = 7
        return today - timedelta(days=delta), last_day.group(0)

    weekday = re.search(
        r"\b(next|this|coming|on|by)?\s*"
        r"(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)\b",
        text,
        re.I,
    )
    if weekday:
        force_next = (weekday.group(1) or "").lower() == "next"
        when = _weekday_on(today, _WEEKDAYS[weekday.group(2).lower()], force_next)
        return when, weekday.group(0)

    day_only = re.search(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", text, re.I)
    if day_only:
        parsed = _day_in_month(today, int(day_only.group(1)))
        if parsed:
            return parsed, day_only.group(0)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _numeric_date(first: int, second: int, year: int) -> date | None:
    if year < 100:
        year += 2000
    if first > 12 and second <= 12:
        return _safe_date(year, second, first)
    if second > 12 and first <= 12:
        return _safe_date(year, first, second)
    if first <= 12 and second <= 12:
        return _safe_date(year, second, first)
    return None


def _named_month(day: int, month_name: str, year_text: str | None, today: date) -> date | None:
    month = _MONTHS[month_name.lower()[:3]]
    year = int(year_text) if year_text else today.year
    parsed = _safe_date(year, month, day)
    if parsed and not year_text and parsed < today:
        parsed = _safe_date(year + 1, month, day)
    return parsed


def _end_of_month(today: date) -> date:
    if today.month == 12:
        return date(today.year, 12, 31)
    return date(today.year, today.month + 1, 1) - timedelta(days=1)


def _shift_month(today: date, months: int) -> date:
    index = today.month - 1 + months
    year = today.year + index // 12
    month = index % 12 + 1
    last = _end_of_month(date(year, month, 1)).day
    return date(year, month, min(today.day, last))


def _weekday_on(today: date, weekday: int, force_next: bool) -> date:
    delta = (weekday - today.weekday()) % 7
    if force_next and delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def _day_in_month(today: date, day: int) -> date | None:
    year, month = today.year, today.month
    parsed = _safe_date(year, month, day)
    if parsed and parsed < today:
        month += 1
        if month == 13:
            month = 1
            year += 1
        parsed = _safe_date(year, month, day)
    return parsed


def _quantity(text: str, awaiting: str | None, description: str = "") -> tuple[str | None, str | None]:
    labelled = re.search(rf"\b(?:qty|quantity)\s*(\d+(?:\.\d+)?)(?:\s*({_UOM}))?\b", text, re.I)
    if labelled:
        return labelled.group(1), (labelled.group(2) or "").upper() or None
    with_uom = re.search(rf"\b(\d+(?:\.\d+)?)\s*({_UOM})\b", text, re.I)
    if with_uom:
        return with_uom.group(1), with_uom.group(2).upper()
    if awaiting == "quantity":
        bare = re.search(r"(\d+(?:\.\d+)?)", text)
        if bare:
            return bare.group(1), None
    leading = re.match(
        r"^(?:please\s+)?(?:(?:i|we)\s+)?(?:(?:need|want|order|buy|get|raise)\s+)?(\d+(?:\.\d+)?)\b(?!\s*(?:mm|inch))",
        text.strip(),
        re.I,
    )
    if leading:
        return leading.group(1), None
    if description or awaiting in {"plant", "need_by", "suggest", "urgency"}:
        ignore = set(re.findall(r"\d+(?:\.\d+)?", description))
        numbers = [number for number in re.findall(r"\b(\d+(?:\.\d+)?)\b", text) if number not in ignore]
        if len(numbers) == 1:
            return numbers[0], None
    return None, None


def _blank_quantity(text: str) -> str:
    text = re.sub(rf"\b(?:qty|quantity)\s*\d+(?:\.\d+)?(?:\s*(?:{_UOM}))?\b", " ", text, flags=re.I)
    text = re.sub(rf"\b\d+(?:\.\d+)?\s*(?:{_UOM})\b", " ", text, flags=re.I)
    text = re.sub(
        r"^(?:please\s+)?(?:(?:i|we)\s+)?(?:(?:need|want|order|buy|get|raise)\s+)?\d+(?:\.\d+)?\b",
        " ",
        text,
        flags=re.I,
    )
    return text
