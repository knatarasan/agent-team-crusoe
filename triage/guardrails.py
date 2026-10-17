"""Hard rules applied in code after the Decider, plus a linter for learned playbook rules."""

import re
from collections.abc import Iterable

from triage.schemas import TriageDecision

# Protected attributes and common proxies a learned rule must never use.
_BANNED = re.compile(
    r"\b(zip|zip ?codes?|postal codes?|postcodes?|neighbou?rhoods?|race|ethnicity|religion|gender|sex|"
    r"national origin|nationality|immigration|disability|marital status|credit score)\b",
    re.IGNORECASE,
)
# Claim-specific identifiers: a rule that names them memorizes the dataset instead of learning a pattern.
_SPECIFIC = re.compile(
    r"\b([A-Z]{3}-[A-Z]{2}-\d{4,}|(claim|row|case|s\.no)\s*#?\s*\d+|#\d+|\d{4}-\d{2}-\d{2}|geico|progressive)\b",
    re.IGNORECASE,
)
COPY_NGRAM = 8  # a rule sharing this many consecutive words with a claim description is copying it


def enforce(decision: TriageDecision, injury: bool) -> TriageDecision:
    d = decision.model_copy()
    if d.coverage != "covered" or d.route in ("deny", "coverage_review", "siu", "adjuster"):
        d.human_signoff = True  # the agent recommends; a human signs off every denial and escalation
    if injury and d.route == "fast_track":
        d.route = "adjuster"  # injury claims always reach a human
        d.human_signoff = True
    return d


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9$%]+", text.lower())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def lint_rule(text: str, descriptions: Iterable[str] = ()) -> list[str]:
    issues = [f"uses protected attribute or proxy: {m.group(0)!r}" for m in _BANNED.finditer(text)]
    issues += [f"names a specific claim, policy, date or carrier: {m.group(0)!r}" for m in _SPECIFIC.finditer(text)]
    grams = _ngrams(text, COPY_NGRAM)
    if grams and any(grams & _ngrams(d, COPY_NGRAM) for d in descriptions):
        issues.append(f"copies {COPY_NGRAM}+ consecutive words from a claim description")
    return issues
