"""Intent model for a chat sentence.

The weights are trained by tools/train_language.py. At runtime the model only
picks an intent. It never chooses an item code, a price, or a supplier.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CLASSES = (
    "answer",
    "buy",
    "bye",
    "cancel",
    "clear",
    "converse",
    "greet",
    "greet_morning",
    "greet_afternoon",
    "greet_evening",
    "help",
    "sorry",
    "thanks",
    "wellbeing",
)
FEATURES = 4096

_WEIGHTS = Path(__file__).with_name("language_model.json")


def _fold(text: str) -> str:
    low = text.lower().replace("'", "'")
    low = low.replace("'", "")
    low = re.sub(r"[^a-z0-9]+", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def _fnv(token: str) -> int:
    value = 2166136261
    for character in token:
        value ^= ord(character)
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def features(text: str) -> dict[int, float]:
    folded = _fold(text)
    counts: dict[int, float] = {}

    def add(token: str) -> None:
        hashed = _fnv(token)
        index = hashed % FEATURES
        sign = -1.0 if hashed & 0x10000 else 1.0
        counts[index] = counts.get(index, 0.0) + sign

    words = folded.split()
    for word in words:
        add("w:" + word)
    for left, right in zip(words, words[1:]):
        add("b:" + left + " " + right)
    padded = f" {folded} "
    for size in (3, 4, 5):
        for start in range(len(padded) - size + 1):
            add("c:" + padded[start : start + size])
    norm = sum(value * value for value in counts.values()) ** 0.5 or 1.0
    return {index: value / norm for index, value in counts.items()}


@lru_cache(maxsize=1)
def _model() -> tuple[list[dict[int, float]], list[float]]:
    payload = json.loads(_WEIGHTS.read_text(encoding="utf-8"))
    weights = [
        {int(index): value for index, value in row.items()}
        for row in payload["weights"]
    ]
    return weights, payload["bias"]


def scores(text: str) -> dict[str, float]:
    weights, bias = _model()
    feats = features(text)
    rated: dict[str, float] = {}
    for label, row, offset in zip(CLASSES, weights, bias):
        total = offset
        for index, value in feats.items():
            total += row.get(index, 0.0) * value
        rated[label] = total
    return rated


def intent_of(text: str) -> str:
    rated = scores(text)
    return max(CLASSES, key=lambda label: rated[label])
