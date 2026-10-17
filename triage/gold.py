"""Turn the free-text `expected_outcome_gold` column into a structured GoldDecision (one-time, cached)."""

from concurrent.futures import ThreadPoolExecutor

from triage import guardrails
from triage.dataset import Claim, load_gold_cache, save_gold_cache
from triage.llm import invoke_json
from triage.schemas import GoldDecision

SYSTEM = """You convert a senior claims adjuster's final decision on an auto claim into structured JSON.
Use only what the adjuster's text says; use the claim description only to decide `injury`.

Return exactly this JSON object:
{
  "coverage": "covered" | "denied" | "coverage_review",
  "fault_pct": integer 0-100 for our insured's share of fault, or null if not covered,
  "pay_other_party": true if the other party's damage is paid under our liability coverage,
  "pay_own_vehicle": true if our insured's car is paid under collision,
  "deductible_applies": true if the insured bears the deductible (false if it is refunded through subrogation),
  "route": "fast_track" | "adjuster" | "siu" | "subrogation" | "coverage_review" | "deny",
  "siu_referral": true if referred to the special investigations unit,
  "subrogation": true if we recover from the other carrier,
  "human_signoff": true if a human must sign off (always true for denials, coverage review, adjuster, injury or SIU),
  "injury": true if anyone reported an injury,
  "rationale": one sentence
}
Route precedence: deny > coverage_review > siu > adjuster > subrogation > fast_track."""


def extract_gold(claim: Claim, llm) -> GoldDecision:
    user = f"State: {claim.state}\nClaim description: {claim.description}\n\nAdjuster's final decision: {claim.gold_text}"
    gold = GoldDecision.model_validate(invoke_json(llm, SYSTEM, user))
    return GoldDecision.model_validate(guardrails.enforce(gold, gold.injury).model_dump())


def prepare_gold(claims: list[Claim], llm, workers: int = 8, force: bool = False) -> tuple[int, int]:
    cache = load_gold_cache()
    todo = [c for c in claims if force or c.id not in cache or cache[c.id]["hash"] != c.gold_hash]
    with ThreadPoolExecutor(workers) as pool:
        for c, gold in zip(todo, pool.map(lambda c: extract_gold(c, llm), todo)):
            cache[c.id] = {"claim_id": c.id, "hash": c.gold_hash, "gold": gold.model_dump()}
    save_gold_cache(cache)
    return len(todo), len(claims)
