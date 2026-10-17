"""Hard rules applied in code after the Decider, plus a linter for learned playbook rules."""

import re

from triage.schemas import TriageDecision

# Protected attributes and common proxies a learned rule must never use.
_BANNED = re.compile(
    r"\b(zip|zip ?codes?|postal codes?|postcodes?|neighbou?rhoods?|race|ethnicity|religion|gender|sex|"
    r"national origin|nationality|immigration|disability|marital status|credit score)\b",
    re.IGNORECASE,
)


def enforce(decision: TriageDecision, injury: bool) -> TriageDecision:
    d = decision.model_copy()
    if d.coverage != "covered" or d.route in ("deny", "coverage_review", "siu", "adjuster"):
        d.human_signoff = True  # the agent recommends; a human signs off every denial and escalation
    if injury:
        d.human_signoff = True
        if d.route == "fast_track":
            d.route = "adjuster"  # injury claims always reach a human
    return d


def lint_rule(text: str) -> list[str]:
    return [f"uses protected attribute or proxy: {m.group(0)!r}" for m in _BANNED.finditer(text)]
