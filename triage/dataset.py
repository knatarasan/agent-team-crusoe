"""Load data/dataset.csv and attach structured gold. Every run uses all rows; there is no split."""

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from triage.config import DATA_CSV, GOLD_CACHE
from triage.schemas import GoldDecision


@dataclass
class Claim:
    id: str
    state: str
    description: str
    gold_text: str
    gold: GoldDecision | None = None

    @property
    def gold_hash(self) -> str:
        return hashlib.sha1(f"{self.description}\n{self.gold_text}".encode()).hexdigest()[:12]


def load_claims(path: Path = DATA_CSV) -> list[Claim]:
    with path.open(newline="") as f:
        return [
            Claim(id=row["s.no"].strip(), state=row["state"].strip().upper(), description=row["description"].strip(),
                  gold_text=row["expected_outcome_gold"].strip())
            for row in csv.DictReader(f)
            if row.get("s.no", "").strip()
        ]


def load_gold_cache(path: Path = GOLD_CACHE) -> dict[str, dict]:
    if not path.exists():
        return {}
    entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {e["claim_id"]: e for e in entries}


def save_gold_cache(entries: dict[str, dict], path: Path = GOLD_CACHE) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in sorted(entries.values(), key=lambda e: _sort_key(e["claim_id"]))))


def _sort_key(cid: str):
    return (0, int(cid)) if cid.isdigit() else (1, cid)


def attach_gold(claims: list[Claim], path: Path = GOLD_CACHE) -> list[Claim]:
    """Attach structured gold from the cache. Fails if any claim is missing or its row changed since extraction."""
    cache = load_gold_cache(path)
    stale = [c.id for c in claims if c.id not in cache or cache[c.id]["hash"] != c.gold_hash]
    if stale:
        raise SystemExit(f"Structured gold missing or out of date for {len(stale)} claim(s) (e.g. {stale[:5]}). "
                         "Run: uv run triage prepare-gold")
    for c in claims:
        c.gold = GoldDecision.model_validate(cache[c.id]["gold"])
    return claims

