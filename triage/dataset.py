"""Load data/dataset.csv, attach structured gold, and split into train / val / test."""

import csv
import hashlib
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from triage.config import DATA_CSV, GOLD_CACHE
from triage.schemas import GoldDecision

SMOKE_THRESHOLD = 30  # below this many claims, splits are not meaningful


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
    with path.open(newline="", encoding="utf-8") as f:
        return [
            Claim(id=row["s.no"].strip(), state=row["state"].strip().upper(), description=row["description"].strip(),
                  gold_text=row["expected_outcome_gold"].strip())
            for row in csv.DictReader(f)
            if row.get("s.no", "").strip()
        ]


def load_labeled_claims(path: Path) -> list[Claim]:
    """Load a separate, explicitly labeled JSONL benchmark without model-based extraction."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [Claim(str(row["id"]), row["state"].upper(), row["description"], row["gold_text"],
                  GoldDecision.model_validate(row["gold"])) for row in rows]


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


def split(claims: list[Claim], seed: int = 42, ratios=(0.5, 0.25, 0.25)) -> dict[str, list[Claim]]:
    """Stratified by gold route so rare twists land in every split. Tiny datasets use every claim everywhere."""
    if len(claims) < SMOKE_THRESHOLD:
        print(f"[warn] only {len(claims)} claims: smoke mode, train = val = test. "
              "Before/after scores are not a real held-out result.", file=sys.stderr)
        return {"train": claims, "val": claims, "test": claims}
    groups: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        groups[c.gold.route if c.gold else "unknown"].append(c)
    out = {"train": [], "val": [], "test": []}
    rng = random.Random(seed)
    for _, group in sorted(groups.items()):
        group = sorted(group, key=lambda c: _sort_key(c.id))
        rng.shuffle(group)
        n_train = round(len(group) * ratios[0])
        n_val = round(len(group) * ratios[1])
        out["train"] += group[:n_train]
        out["val"] += group[n_train : n_train + n_val]
        out["test"] += group[n_train + n_val :]
    return out
