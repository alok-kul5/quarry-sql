"""Execution accuracy: does the agent's final result hold the gold answer?

Rules, kept strict on purpose:
  * same number of rows, compared as a multiset (row order is ignored)
  * every gold column must match a distinct predicted column; extra columns
    are allowed, so "which zone" passes with or without a count beside it
  * numbers match within 0.011 absolute or 0.05% relative, so rounding to
    cents is fine but a fraction (0.79) is not a percentage (79)
  * strings compare trimmed and case-insensitive
"""
from __future__ import annotations

from itertools import permutations


def _norm(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return None
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        return s.lower()


def _eq(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(0.011, 5e-4 * abs(b))
    return a == b


def _key(v):
    if isinstance(v, float):
        return (0, round(v, 1), "")
    if v is None:
        return (1, 0.0, "")
    return (2, 0.0, v)


def rows_match(gold: list[tuple], pred: list[tuple]) -> bool:
    if len(gold) != len(pred):
        return False
    g = sorted(gold, key=lambda r: [_key(v) for v in r])
    p = sorted(pred, key=lambda r: [_key(v) for v in r])
    if all(all(_eq(a, b) for a, b in zip(rp, rg)) for rp, rg in zip(p, g)):
        return True
    # Sorting on rounded keys can misalign near-equal numbers; fall back to greedy matching.
    left = list(p)
    for rg in g:
        hit = next((i for i, rp in enumerate(left) if all(_eq(a, b) for a, b in zip(rp, rg))), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


def result_matches(gold_rows: list[list], pred_rows: list[list] | None, max_pred_cols: int = 8) -> bool:
    if pred_rows is None:
        return False
    gold = [tuple(_norm(v) for v in r) for r in gold_rows]
    pred = [tuple(_norm(v) for v in r) for r in pred_rows]
    if len(gold) != len(pred):
        return False
    if not gold:
        return True
    gcols, pcols = len(gold[0]), len(pred[0]) if pred else 0
    if pcols < gcols or pcols > max_pred_cols:
        return False
    for combo in permutations(range(pcols), gcols):
        projected = [tuple(r[i] for i in combo) for r in pred]
        if rows_match(gold, projected):
            return True
    return False
