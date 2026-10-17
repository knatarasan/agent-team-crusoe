"""Structured triage decisions: what agents output and what gold is scored as."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Coverage = Literal["covered", "denied", "coverage_review"]
Route = Literal["fast_track", "adjuster", "siu", "subrogation", "coverage_review", "deny"]

_COVERAGE_ALIASES = {"deny": "denied", "denial": "denied", "not_covered": "denied", "review": "coverage_review",
                     "coverage_question": "coverage_review"}
_ROUTE_ALIASES = {"fasttrack": "fast_track", "fast_tracked": "fast_track", "denied": "deny", "denial": "deny",
                  "special_investigations_unit": "siu", "coverage_question": "coverage_review", "review": "coverage_review"}


def _norm(v, aliases: dict):
    if isinstance(v, str):
        v = re.sub(r"[\s\-]+", "_", v.strip().lower())
        return aliases.get(v, v)
    return v


class TriageDecision(BaseModel):
    coverage: Coverage
    fault_pct: int | None = Field(None, ge=0, le=100, description="Our insured's share of fault; null if not covered")
    pay_other_party: bool = False
    pay_own_vehicle: bool = False
    deductible_applies: bool = False
    route: Route
    siu_referral: bool = False
    subrogation: bool = False
    human_signoff: bool = False
    rationale: str = ""
    rules_cited: list[str] = Field(default_factory=list)

    @field_validator("coverage", mode="before")
    @classmethod
    def _norm_coverage(cls, v):
        return _norm(v, _COVERAGE_ALIASES)

    @field_validator("route", mode="before")
    @classmethod
    def _norm_route(cls, v):
        return _norm(v, _ROUTE_ALIASES)

    @field_validator("fault_pct", mode="before")
    @classmethod
    def _pct(cls, v):
        if isinstance(v, str):
            m = re.search(r"\d+", v)
            return int(m.group()) if m else None
        return v

    def brief(self) -> str:
        fault = "n/a" if self.fault_pct is None else f"{self.fault_pct}%"
        flags = [k for k in ("pay_other_party", "pay_own_vehicle", "deductible_applies", "siu_referral",
                             "subrogation", "human_signoff") if getattr(self, k)]
        return f"{self.coverage} | fault {fault} | route {self.route} | {', '.join(flags) or 'no flags'}"


class GoldDecision(TriageDecision):
    """Adjuster's final decision. `injury` lets the scorer price injury-to-fast-track mistakes."""

    injury: bool = False
