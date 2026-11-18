from __future__ import annotations

import re

from corvo.models import Catalog, Item, PRLine

STOP = {
    "urgent",
    "for",
    "maintenance",
    "as",
    "per",
    "sample",
    "repeat",
    "the",
    "a",
    "an",
    "of",
    "and",
    "to",
    "with",
    "at",
    "on",
    "by",
    "in",
    "from",
}
ALIASES = {
    "brg": "bearing",
    "rod": "electrode",
    "hyd": "hydraulic",
    "pneu": "pneumatic",
}


def tokens(text: str) -> list[str]:
    cleaned = text.lower().replace('"', " inch ").replace("'", " ")
    cleaned = cleaned.replace("-", " ")
    cleaned = re.sub(r"[()+,;:]", " ", cleaned)
    result: list[str] = []
    for raw in cleaned.split():
        raw = raw.strip(".")
        if not raw or raw in STOP:
            continue
        result.append(ALIASES.get(raw, raw))
    return result


def _edit_distance(left: str, right: str) -> int:
    if abs(len(left) - len(right)) > 2:
        return 3
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def _coverage(query: set[str], item: set[str]) -> tuple[int, bool] | None:
    """Score when every typed word is in the item, allowing a small spelling slip."""
    if not query:
        return None
    query_numbers = {token for token in query if any(char.isdigit() for char in token)}
    item_numbers = {token for token in item if any(char.isdigit() for char in token)}
    if not query_numbers <= item_numbers:
        return None
    words = [token for token in item if not any(char.isdigit() for char in token)]
    slipped = False
    for token in query:
        if token in item:
            continue
        if any(char.isdigit() for char in token):
            return None
        limit = 2 if len(token) >= 8 else 1 if len(token) >= 5 else 0
        if limit == 0 or not any(_edit_distance(token, word) <= limit for word in words):
            return None
        slipped = True
    score = sum(3 if any(char.isdigit() for char in token) else 1 for token in query)
    if query <= item:
        score += 5
    elif slipped:
        score += 4
    return score, (not slipped and query == item)


def _score(query: set[str], item: set[str]) -> int:
    query_numbers = {token for token in query if any(char.isdigit() for char in token)}
    item_numbers = {token for token in item if any(char.isdigit() for char in token)}
    if not query_numbers <= item_numbers:
        return 0
    shared = query & item
    if not shared:
        return 0
    score = 0
    for token in shared:
        score += 3 if any(char.isdigit() for char in token) else 1
    if query <= item or item <= query:
        score += 5
    return score


class Matcher:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._tokens = {code: set(tokens(item.description)) for code, item in catalog.items.items()}

    def match_text(self, description: str, uom: str = "") -> tuple[Item | None, str]:
        code = re.search(r"ITM-\d+", description, re.I)
        if code and code.group(0).upper() in self.catalog.items:
            return self.catalog.items[code.group(0).upper()], "item code in the text"
        query = set(tokens(description))
        if not query:
            return None, "empty description"
        ranked: list[tuple[int, str]] = []
        for item_code, item_tokens in self._tokens.items():
            item = self.catalog.items[item_code]
            if uom and item.uom != uom:
                continue
            score = _score(query, item_tokens)
            if score:
                ranked.append((score, item_code))
        if not ranked:
            return None, "no item master match"
        ranked.sort(key=lambda pair: (-pair[0], pair[1]))
        best_score, best_code = ranked[0]
        if best_score < 3:
            return None, "no item master match"
        return self.catalog.items[best_code], f"matched description to {best_code}"

    def close_alternatives(self, description: str, uom: str = "") -> list[Item]:
        query = set(tokens(description))
        ranked: list[tuple[int, str]] = []
        for item_code, item_tokens in self._tokens.items():
            item = self.catalog.items[item_code]
            if uom and item.uom != uom:
                continue
            score = _score(query, item_tokens)
            if score:
                ranked.append((score, item_code))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]))
        if len(ranked) < 2:
            return []
        if ranked[1][0] >= ranked[0][0] * 0.9 and ranked[0][0] == ranked[1][0]:
            return [self.catalog.items[code] for _, code in ranked[:3]]
        return []

    def interpret(self, description: str) -> tuple[str, list[Item]]:
        """exact when the words are the catalogue name, suggest for a close or partial name."""
        query = set(tokens(description))
        if not query:
            return "none", []
        ranked: list[tuple[int, str, bool]] = []
        for item_code, item_tokens in self._tokens.items():
            covered = _coverage(query, item_tokens)
            if covered is None:
                continue
            score, exact = covered
            ranked.append((score, item_code, exact))
        if not ranked:
            return "none", []
        ranked.sort(key=lambda row: (-row[0], row[1]))
        top = ranked[0][0]
        leaders = [row for row in ranked if row[0] == top][:3]
        items = [self.catalog.items[row[1]] for row in leaders]
        if len(leaders) == 1 and leaders[0][2]:
            return "exact", items
        return "suggest", items

    def apply(self, line: PRLine) -> None:
        if line.item_code and line.item_code in self.catalog.items:
            line.resolved_item = line.item_code
            line.match_note = "item code"
            return
        item, note = self.match_text(line.description, line.uom)
        line.resolved_item = item.code if item else None
        line.match_note = note
