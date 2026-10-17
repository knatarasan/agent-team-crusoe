"""Cost-weighted scoring of a triage decision against the adjuster's gold (weights from plan.md)."""

from collections import defaultdict
from dataclasses import dataclass, field

from triage.schemas import GoldDecision, TriageDecision

WEIGHTS = {
    "wrongful_denial": 10,
    "wrongful_payment": 5,
    "missed_payment": 5,
    "missed_fraud": 5,
    "injury_fast_tracked": 3,
    "missing_human_signoff": 3,
    "missed_subrogation": 2,
    "fault_off": 1,
    "deductible_wrong": 1,
    "over_escalated": 1,
    "wrong_route": 1,
}
MAX_PENALTY = 20
FAULT_TOLERANCE = 10
DECISION_FIELDS = set(TriageDecision.model_fields) - {"rationale", "rules_cited"}


@dataclass
class ClaimScore:
    claim_id: str
    category: str
    penalty: int
    mistakes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return max(0.0, 1 - self.penalty / MAX_PENALTY)


def score_claim(claim_id: str, pred: TriageDecision, gold: GoldDecision) -> ClaimScore:
    m: list[str] = []
    if pred.coverage == "denied" and gold.coverage != "denied":
        m.append("wrongful_denial")
    if (
        (pred.coverage == "covered" and gold.coverage != "covered")
        or (pred.pay_other_party and not gold.pay_other_party)
        or (pred.pay_own_vehicle and not gold.pay_own_vehicle)
    ):
        m.append("wrongful_payment")
    if gold.siu_referral and not pred.siu_referral:
        m.append("missed_fraud")
    if gold.injury and pred.route == "fast_track":
        m.append("injury_fast_tracked")
    if gold.subrogation and not pred.subrogation:
        m.append("missed_subrogation")
    if gold.human_signoff and not pred.human_signoff:
        m.append("missing_human_signoff")

    # Finer checks only matter when both agree the claim is covered.
    if gold.coverage == "covered" and pred.coverage == "covered":
        if (gold.pay_other_party and not pred.pay_other_party) or (gold.pay_own_vehicle and not pred.pay_own_vehicle):
            m.append("missed_payment")
        if gold.fault_pct is not None and (pred.fault_pct is None or abs(pred.fault_pct - gold.fault_pct) > FAULT_TOLERANCE):
            m.append("fault_off")
        if pred.deductible_applies != gold.deductible_applies:
            m.append("deductible_wrong")

    if gold.route == "fast_track" and pred.route != "fast_track":
        m.append("over_escalated")
    elif pred.route != gold.route and not {"injury_fast_tracked", "missed_fraud", "wrongful_denial"} & set(m):
        m.append("wrong_route")

    return ClaimScore(claim_id, gold.route, sum(WEIGHTS[k] for k in m), m)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


@dataclass
class Report:
    scores: list[ClaimScore]
    preds: dict[str, TriageDecision] = field(default_factory=dict)
    golds: dict[str, GoldDecision] = field(default_factory=dict)
    errors: int = 0

    @property
    def cost_weighted_score(self) -> float:
        return sum(s.score for s in self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def wrongful_denials(self) -> int:
        return sum("wrongful_denial" in s.mistakes for s in self.scores)

    def by_category(self) -> dict[str, float]:
        groups = defaultdict(list)
        for s in self.scores:
            groups[s.category].append(s.score)
        return {k: round(sum(v) / len(v), 3) for k, v in sorted(groups.items())}

    def summary(self) -> dict:
        n = len(self.scores)
        p, g = self.preds, self.golds
        straight_through = sum(d.route == "fast_track" and not d.human_signoff for d in p.values())
        siu_pred = [cid for cid, d in p.items() if d.siu_referral]
        subro_gold = [cid for cid, d in g.items() if d.subrogation]
        return {
            "claims": n,
            "cost_weighted_score": round(self.cost_weighted_score, 3),
            "exact_match": _ratio(sum(
                not s.mistakes and s.claim_id in p and s.claim_id in g
                and all(getattr(p[s.claim_id], name) == getattr(g[s.claim_id], name) for name in DECISION_FIELDS)
                for s in self.scores
            ), n),
            "wrongful_denials": self.wrongful_denials,
            "straight_through_rate": _ratio(straight_through, len(p)),
            "siu_hit_rate": _ratio(sum(g[c].siu_referral for c in siu_pred if c in g), len(siu_pred)),
            "subrogation_caught": _ratio(sum(p[c].subrogation for c in subro_gold if c in p), len(subro_gold)),
            "by_category": self.by_category(),
            "errors": self.errors,
        }
