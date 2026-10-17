"""Offline tests: no Crusoe calls. The loop test uses a stub team and stub supervisor."""

import pytest

from triage import guardrails
from triage.dataset import Claim, split
from triage.llm import parse_json
from triage.loop import improve
from triage.playbook import Playbook, Rule
from triage.schemas import GoldDecision, TriageDecision
from triage.scorer import Report, score_claim
from triage.supervisor import Edit, apply_edit

BASELINE = GoldDecision(coverage="covered", fault_pct=100, pay_other_party=True, pay_own_vehicle=True,
                        deductible_applies=True, route="fast_track")
EXCLUDED = GoldDecision(coverage="denied", route="deny", human_signoff=True)
SWOOP = GoldDecision(coverage="covered", fault_pct=50, pay_other_party=True, pay_own_vehicle=True,
                     deductible_applies=True, route="siu", siu_referral=True, human_signoff=True, injury=True)
HIT = GoldDecision(coverage="covered", fault_pct=0, pay_own_vehicle=True, route="subrogation", subrogation=True)
NAIVE = TriageDecision(coverage="covered", fault_pct=100, pay_other_party=True, pay_own_vehicle=True,
                       deductible_applies=True, route="fast_track")


def test_parse_json_handles_fences_and_prose():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('<think>hmm {x}</think>{"edits": []}') == {"edits": []}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_schema_normalizes_model_output():
    d = TriageDecision.model_validate({"coverage": "Deny", "route": "Fast-Track", "fault_pct": "100%"})
    assert (d.coverage, d.route, d.fault_pct) == ("denied", "fast_track", 100)


def test_perfect_prediction_scores_100():
    for gold in (BASELINE, EXCLUDED, SWOOP, HIT):
        s = score_claim("x", TriageDecision.model_validate(gold.model_dump()), gold)
        assert s.score == 1.0 and not s.mistakes


def test_day1_mistakes_are_cost_weighted():
    assert "wrongful_payment" in score_claim("2", NAIVE, EXCLUDED).mistakes
    assert {"missed_fraud", "injury_fast_tracked"} <= set(score_claim("3", NAIVE, SWOOP).mistakes)
    assert {"missed_subrogation", "deductible_wrong"} <= set(score_claim("4", NAIVE, HIT).mistakes)
    deny = TriageDecision(coverage="denied", route="deny", human_signoff=True)
    wrongful = score_claim("1", deny, BASELINE)
    assert "wrongful_denial" in wrongful.mistakes
    assert wrongful.penalty > score_claim("2", NAIVE, EXCLUDED).penalty


def test_over_escalation_is_cheap():
    s = score_claim("1", NAIVE.model_copy(update={"route": "adjuster"}), BASELINE)
    assert s.mistakes == ["over_escalated"] and s.score == pytest.approx(0.95)


@pytest.mark.parametrize("field", ["pay_other_party", "pay_own_vehicle"])
def test_missing_required_payment_is_penalized(field):
    pred = NAIVE.model_copy(update={field: False})
    score = score_claim("1", pred, BASELINE)
    assert "missed_payment" in score.mistakes and score.score < 1.0


def test_missing_required_human_signoff_is_penalized():
    pred = TriageDecision.model_validate(EXCLUDED.model_dump()).model_copy(update={"human_signoff": False})
    score = score_claim("2", pred, EXCLUDED)
    assert "missing_human_signoff" in score.mistakes and score.score < 1.0


def test_exact_match_is_stricter_than_fault_scoring_tolerance():
    pred = NAIVE.model_copy(update={"fault_pct": 95})
    score = score_claim("1", pred, BASELINE)
    report = Report([score], {"1": pred}, {"1": BASELINE})
    assert score.score == 1.0
    assert report.summary()["exact_match"] == 0.0
    report.preds["1"] = NAIVE.model_copy(update={"rationale": "different explanation", "rules_cited": ["R1"]})
    assert report.summary()["exact_match"] == 1.0


def test_agent_error_cannot_receive_full_credit_for_review_fallback():
    from triage.evaluate import evaluate

    gold = GoldDecision(coverage="coverage_review", route="coverage_review", human_signoff=True)
    claim = Claim("error", "CA", "policy status unknown", "review", gold)

    def broken_team(claim, playbook):
        raise ValueError("invalid model output")

    report = evaluate([claim], Playbook(), broken_team, workers=1)
    assert report.errors == 1
    assert report.cost_weighted_score == 0.0
    assert report.summary()["exact_match"] == 0.0
    assert "agent_error" in report.scores[0].mistakes


def test_guardrails():
    assert guardrails.enforce(TriageDecision(coverage="denied", route="deny"), injury=False).human_signoff
    fixed = guardrails.enforce(NAIVE, injury=True)
    assert fixed.route == "adjuster" and fixed.human_signoff
    assert guardrails.lint_rule("Refer claims from ZIP code 77001 to SIU")
    assert not guardrails.lint_rule("Deny when the driver is a signed excluded driver")


def test_injury_requires_human_signoff_on_subrogation_route():
    decision = TriageDecision.model_validate(HIT.model_dump())
    fixed = guardrails.enforce(decision, injury=True)
    assert fixed.route == "subrogation" and fixed.human_signoff
    assert not decision.human_signoff


@pytest.mark.parametrize("route,injury", [("adjuster", False), ("fast_track", True)])
def test_gold_extraction_applies_shared_safety_rules(monkeypatch, route, injury):
    from triage import gold as goldmod

    raw = BASELINE.model_copy(update={"route": route, "injury": injury}).model_dump()
    monkeypatch.setattr(goldmod, "invoke_json", lambda *args: raw)
    result = goldmod.extract_gold(Claim("1", "CA", "claim", "adjuster decision"), object())
    assert result.route == "adjuster" and result.human_signoff
    assert result.injury == injury


def test_playbook_filtering_and_edits():
    pb = Playbook(rules=[Rule(id="R1", text="CA rear driver presumed at fault", role="liability", state="CA")])
    assert pb.rules_for("liability", "TX") == [] and "[R1]" in pb.render("liability", "CA")
    pb1 = apply_edit(pb, Edit(op="add", text="Check excluded drivers", role="coverage"), 1)
    assert [r.id for r in pb1.rules] == ["R1", "R2"] and pb.rules[0].id == "R1" and len(pb.rules) == 1
    pb2 = apply_edit(pb1, Edit(op="retire", rule_id="R1"), 2)
    assert [r.id for r in pb2.active()] == ["R2"]
    with pytest.raises(ValueError):
        apply_edit(pb2, Edit(op="retire", rule_id="R9"), 3)


def _claims(n: int) -> list[Claim]:
    golds = [BASELINE, EXCLUDED, SWOOP, HIT]
    return [Claim(str(i), "CA", f"claim {i} excluded" if golds[i % 4] is EXCLUDED else f"claim {i}", "gold",
                  golds[i % 4]) for i in range(n)]


def test_split_is_stratified_and_disjoint():
    s = split(_claims(80))
    ids = [set(c.id for c in s[k]) for k in ("train", "val", "test")]
    assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
    for k in ("train", "val", "test"):
        assert {c.gold.route for c in s[k]} == {"fast_track", "deny", "siu", "subrogation"}


def test_loop_keeps_good_rule_and_rejects_overreach(tmp_path):
    """Stub team obeys the playbook literally; the supervisor proposes one overreaching and one good rule."""

    def team(claim: Claim, pb: Playbook) -> TriageDecision:
        texts = " ".join(r.text for r in pb.active())
        if "deny everything" in texts:
            return TriageDecision(coverage="denied", route="deny", human_signoff=True)
        if "excluded" in claim.description and "excluded driver" in texts:
            return TriageDecision(coverage="denied", route="deny", human_signoff=True)
        return NAIVE

    def propose(fails, pb):
        return [Edit(op="add", text="deny everything to be safe", role="coverage"),
                Edit(op="add", text="Deny when an excluded driver was driving", role="coverage",
                     source_claim_ids=[f["claim_id"] for f in fails[:2]]),
                Edit(op="add", text="Send ZIP code 90210 claims to SIU", role="fraud")]

    claims = _claims(40)
    result = improve(claims, claims, Playbook(), team, propose, cycles=2, workers=2, playbook_dir=tmp_path,
                     echo=lambda *_: None)
    assert result.final.version == 1
    assert [r.text for r in result.final.active()] == ["Deny when an excluded driver was driving"]
    reasons = {d.edit: d.reason for d in result.log}
    assert any("wrongful denials" in r for e, r in reasons.items() if "deny everything" in e)
    assert any("protected attribute" in r for e, r in reasons.items() if "ZIP" in e)
    assert result.val_history[-1] > result.val_history[0]
    assert (tmp_path / "v1.yaml").exists()


def test_langgraph_team_wiring_with_fake_model():
    """Fake chat model replies in graph order; checks routing, rule citation merge and guardrails."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from triage.agents import make_triage_fn

    replies = [
        '{"summary": "rear-end", "insured_vehicle_position": "rear", "injury": true, "facts": []}',
        '{"coverage": "covered", "covers_other_party": true, "covers_own_vehicle": true, "reason": "ok", "rules_cited": ["R1"]}',
        '{"fault_pct": 100, "subrogation": false, "reason": "rear", "rules_cited": []}',
        '{"siu_referral": false, "reason": "none", "rules_cited": []}',
        '```json\n{"coverage": "covered", "fault_pct": 100, "pay_other_party": true, "pay_own_vehicle": true, '
        '"deductible_applies": true, "route": "fast_track", "siu_referral": false, "subrogation": false, '
        '"human_signoff": false, "rationale": "simple", "rules_cited": []}\n```',
    ]
    llm = GenericFakeChatModel(messages=iter(AIMessage(r) for r in replies))
    pb = Playbook(rules=[Rule(id="R1", text="Check the policy dates", role="coverage")])
    d = make_triage_fn(llm)(Claim("1", "CA", "Our insured rear-ended a car; driver has neck pain.", "gold"), pb)
    assert d.rules_cited == ["R1"]
    assert d.route == "adjuster" and d.human_signoff  # injury claim cannot be fast-tracked


def test_denied_claim_skips_liability_and_fraud():
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from triage.agents import make_triage_fn

    replies = [
        '{"summary": "excluded driver", "insured_vehicle_position": "rear", "injury": false, "facts": []}',
        '{"coverage": "denied", "covers_other_party": false, "covers_own_vehicle": false, "reason": "excluded", "rules_cited": []}',
        '{"coverage": "denied", "fault_pct": null, "route": "deny", "rationale": "excluded driver", "rules_cited": []}',
    ]
    llm = GenericFakeChatModel(messages=iter(AIMessage(r) for r in replies))
    d = make_triage_fn(llm)(Claim("2", "TX", "Excluded nephew was driving.", "gold"), Playbook())
    assert (d.coverage, d.route, d.human_signoff) == ("denied", "deny", True)
