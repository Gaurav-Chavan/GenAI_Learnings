"""Reciprocal rank fusion: merge several ranked lists into one without comparing their (incomparable) scores."""
from __future__ import annotations

RRF_K = 60  # the usual constant; larger values flatten the advantage of being first in a list


def rrf(ranked_lists: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float, list[str]]]:
    """ranked_lists maps an arm name to its keys, best first. Returns (key, score, arms that found it), best first.

    An item found by more than one arm collects a score from each, so agreement between the keyword and graph arms lifts it above items only one arm saw.
    """
    scores: dict[str, float] = {}
    arms: dict[str, list[str]] = {}
    for arm, keys in ranked_lists.items():
        for rank, key in enumerate(dict.fromkeys(keys), start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            arms.setdefault(key, []).append(arm)
    return sorted(((key, score, arms[key]) for key, score in scores.items()), key=lambda t: (-t[1], t[0]))
