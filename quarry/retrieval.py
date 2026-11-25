"""Schema retrieval: pick the columns and data values a question is about.

Two small indexes, both built once from the live database:
  * column docs  - "table column type description" scored with BM25
  * value index  - distinct values of low-cardinality text columns, so
                   "JFK" in a question finds zones.zone = 'JFK Airport'
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .db import Database
from .tools import WITHHELD, sanitize

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an the of in on at to for by with and or is are was were what which how many much "
            "per each from that this do does did me show give list top most least by than".split())
# Words people use that the column descriptions do not.
ALIASES = {
    "trips.total_amount": "revenue money spent paid cost price earnings dollars",
    "trips.fare_amount": "fare meter price cost",
    "trips.tip_amount": "tips tipping gratuity",
    "trips.trip_distance": "distance miles long far",
    "trips.passenger_count": "passengers riders people group",
    "trips.pickup_at": "time hour day date weekday when start month",
    "trips.dropoff_at": "end arrival duration minutes",
    "trips.pickup_zone_id": "from origin start pickup pick neighborhood neighbourhood area",
    "trips.dropoff_zone_id": "to destination dropoff drop neighborhood neighbourhood area",
    "trips.taxi_type": "yellow green cab boro",
    "trips.airport_fee": "airport laguardia jfk",
    "trips.congestion_surcharge": "congestion surcharge",
    "zones.borough": "borough manhattan brooklyn queens bronx staten island",
    "zones.zone": "zone neighborhood neighbourhood area place airport",
    "payment_types.description": "payment pay cash card credit dispute",
    "vendors.name": "vendor company provider",
    "rate_codes.description": "rate code negotiated jfk newark flat",
}
JOIN_HINTS = [
    "trips.pickup_zone_id = zones.zone_id (and trips.dropoff_zone_id = zones.zone_id)",
    "trips.payment_type = payment_types.payment_type",
    "trips.rate_code_id = rate_codes.rate_code_id",
    "trips.vendor_id = vendors.vendor_id",
]


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower().replace("_", " ")) if w not in _STOP]


@dataclass
class Hit:
    table: str
    column: str
    type: str
    comment: str
    score: float


class SchemaRetriever:
    def __init__(self, db: Database, k1: float = 1.4, b: float = 0.6):
        self.db = db
        self.k1, self.b = k1, b
        self.docs: list[tuple[Hit, list[str]]] = []
        for t in db.schema():
            for c in t.columns:
                text = f"{t.name} {c.name} {c.type} {c.comment} {t.comment} {ALIASES.get(f'{t.name}.{c.name}', '')}"
                self.docs.append((Hit(t.name, c.name, c.type, c.comment, 0.0), tokens(text)))
        self.df = Counter(w for _, toks in self.docs for w in set(toks))
        self.avgdl = sum(len(t) for _, t in self.docs) / max(len(self.docs), 1)
        self.values: list[tuple[str, str, str, set[str]]] = []
        for t in db.schema():
            for c in t.columns:
                if c.type != "VARCHAR":
                    continue
                vals = db.distinct_values(t.name, c.name, limit=301)
                if len(vals) > 300:
                    continue
                for v in vals:
                    toks = set(tokens(str(v)))
                    if toks:
                        self.values.append((t.name, c.name, str(v), toks))

    def columns(self, question: str, k: int = 12) -> list[Hit]:
        q = tokens(question)
        n = len(self.docs)
        scored = []
        for hit, toks in self.docs:
            tf = Counter(toks)
            s = 0.0
            for w in q:
                if w not in tf:
                    continue
                idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                s += idf * tf[w] * (self.k1 + 1) / (tf[w] + self.k1 * (1 - self.b + self.b * len(toks) / self.avgdl))
            if s > 0:
                scored.append(Hit(hit.table, hit.column, hit.type, hit.comment, round(s, 3)))
        scored.sort(key=lambda h: -h.score)
        return scored[:k]

    def matching_values(self, question: str, k: int = 8) -> list[tuple[str, str, str]]:
        q = set(tokens(question))
        hits = []
        for table, col, val, toks in self.values:
            overlap = len(q & toks)
            if overlap and (overlap == len(toks) or overlap >= 2 or len(toks) <= 2):
                hits.append((overlap / len(toks) + overlap, table, col, val))
        hits.sort(key=lambda h: -h[0])
        return [(t, c, v) for _, t, c, v in hits[:k]]

    def context(self, question: str) -> str:
        """A compact schema block for the system prompt."""
        hits = self.columns(question)
        tables = list(dict.fromkeys(["trips"] + [h.table for h in hits]))
        values = self.matching_values(question)
        tables += [t for t, _, _ in values if t not in tables]
        lines = ["Relevant schema (retrieved for this question):"]
        for t in tables:
            table = self.db.table(t)
            if not table:
                continue
            cols = ", ".join(f"{c.name} {c.type}" + (f" -- {c.comment}" if c.comment else "")
                             for c in table.columns)
            lines.append(f"- {t} ({table.row_count:,} rows): {cols}")
        joins = [j for j in JOIN_HINTS if any(f"{t}." in j for t in tables if t != "trips")]
        if joins:
            lines.append("Joins: " + "; ".join(joins))
        if values:
            # Values come from the data, so they pass through the same filter as tool results.
            safe = [(t, c, sanitize(v)[0]) for t, c, v in values]
            lines.append("Values in the data that match words in the question: "
                         + "; ".join(f"{t}.{c} = '{v}'" for t, c, v in safe if v != WITHHELD))
        return "\n".join(lines)
