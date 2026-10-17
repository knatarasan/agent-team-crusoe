"""Offline tests: no Crusoe calls. The run tests use a stub team and stub supervisor."""

import pytest

from triage import guardrails
from triage.dataset import Claim
from triage.llm import parse_json
from triage.playbook import Playbook, Rule
from triage.schemas import GoldDecision, TriageDecision
from triage.scorer import score_claim
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


def test_guardrails():
    assert guardrails.enforce(TriageDecision(coverage="denied", route="deny"), injury=False).human_signoff
    fixed = guardrails.enforce(NAIVE, injury=True)
    assert fixed.route == "adjuster" and fixed.human_signoff
    assert guardrails.lint_rule("Refer claims from ZIP code 77001 to SIU")
    assert not guardrails.lint_rule("Deny when the driver is a signed excluded driver")


def test_playbook_filtering_and_edits():
    pb = Playbook(rules=[Rule(id="R1", text="CA rear driver presumed at fault", role="liability", state="CA")])
    assert pb.rules_for("liability", "TX") == [] and "[R1]" in pb.render("liability", "CA")
    pb1 = apply_edit(pb, Edit(op="add", text="Check excluded drivers", role="coverage"), 1)
    assert [r.id for r in pb1.rules] == ["R1", "R2"] and pb.rules[0].id == "R1" and len(pb.rules) == 1
    pb2 = apply_edit(pb1, Edit(op="retire", rule_id="R1"), 2)
    assert [r.id for r in pb2.active()] == ["R2"]
    with pytest.raises(ValueError):
        apply_edit(pb2, Edit(op="retire", rule_id="R9"), 3)


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


def test_generality_linter_rejects_memorized_rules():
    desc = ["Our insured rear-ended a sedan stopped at a red light at about 15 mph; no injuries and a police report was filed."]
    assert guardrails.lint_rule("Claim 12 should be denied", desc)
    assert guardrails.lint_rule("Deny policy GEI-CA-104512", desc)
    assert guardrails.lint_rule("Pay when Party B is insured by Progressive", desc)
    assert guardrails.lint_rule("If our insured rear-ended a sedan stopped at a red light at about 15 mph, fast-track", desc)
    assert not guardrails.lint_rule("If the lead car was lawfully stopped and nobody is hurt, fast-track", desc)


def test_instruct_edit_sets_role_prompt_addon():
    pb = apply_edit(Playbook(), Edit(op="instruct", role="coverage", text="Always compare dates with the grace period."), 1)
    assert pb.role_instructions == {"coverage": "Always compare dates with the grace period."}
    assert "grace period" in pb.render("coverage", "TX") and pb.render("liability", "TX") == ""


# --- run-by-run mechanism -------------------------------------------------------------------------

def _claims() -> list[Claim]:
    golds = [BASELINE, EXCLUDED, SWOOP, HIT]
    return [Claim(str(i), "CA", "excluded nephew was driving" if golds[i % 4] is EXCLUDED else f"rear-end case {i}",
                  "gold", golds[i % 4]) for i in range(20)]


def _team(claim: Claim, pb: Playbook) -> TriageDecision:
    """Stub team that obeys the config literally."""
    texts = " ".join(r.text for r in pb.active()) + " " + " ".join(pb.role_instructions.values())
    if "deny everything" in texts:
        return TriageDecision(coverage="denied", route="deny", human_signoff=True)
    if "excluded" in claim.description and "excluded driver" in texts:
        return TriageDecision(coverage="denied", route="deny", human_signoff=True)
    return NAIVE


def test_runs_improve_keep_best_and_roll_back(tmp_path):
    from triage import playbook as pbmod
    from triage.runs import history_report, load_state, read_history, run_once

    pb_dir, runs_dir = tmp_path / "playbook", tmp_path / "runs"
    pbmod.save(Playbook(), pb_dir)
    proposals = iter([
        [Edit(op="add", text="Deny when an excluded driver was driving", role="coverage"),
         Edit(op="add", text="Send claim 7 to SIU", role="fraud")],                 # second one: linter rejects
        [Edit(op="instruct", role="coverage", text="When unsure, deny everything to be safe")],  # overreach
        [Edit(op="add", text="Pay collision and pursue subrogation when our insured was hit", role="liability")],
    ])
    seen_history: list[list] = []

    def propose(fails, pb, history):
        seen_history.append(list(history))
        return next(proposals)

    kw = dict(threshold=0.02, workers=2, runs_dir=runs_dir, playbook_dir=pb_dir)
    r1 = run_once(_claims(), _team, propose, **kw)
    r2 = run_once(_claims(), _team, propose, **kw)
    r3 = run_once(_claims(), _team, propose, **kw)

    assert (r1["status"], r2["status"], r3["status"]) == ("baseline", "kept", "rolled back")
    assert r2["avg_score"] > r1["avg_score"] and r3["wrongful_denials"] > 0
    state = load_state(runs_dir)
    assert state["best"]["version"] == 1  # the excluded-driver rule survives; the overreach was rolled back
    assert seen_history[2][-1]["result"] == "rolled back"  # supervisor sees what failed
    nxt = pbmod.load(state["next_version"], pb_dir)  # proposed on top of v1, not on top of the bad v2
    assert [r.text for r in nxt.active()][0] == "Deny when an excluded driver was driving"
    assert "deny everything" not in " ".join(nxt.role_instructions.values())

    assert [h["run"] for h in read_history(runs_dir)] == ["1", "2", "3"]
    for f in ("config.yaml", "results.csv", "summary.json", "changes.md", "failures.json"):
        assert (runs_dir / "run_002" / f).exists()
    assert "names a specific claim" in (runs_dir / "run_001" / "changes.md").read_text()
    report = history_report(runs_dir, tmp_path / "history.md")
    assert "xychart-beta" in report and "rolled back" in report
    html = (tmp_path / "dashboard.html").read_text()
    assert "Claim grid" in html and "Next run will test" in html and html.count("<svg") == 1


def test_rolled_back_edits_cannot_be_reproposed(tmp_path):
    from triage import playbook as pbmod
    from triage.runs import load_state, run_once

    pb_dir, runs_dir = tmp_path / "playbook", tmp_path / "runs"
    pbmod.save(Playbook(), pb_dir)
    bad = Edit(op="instruct", role="coverage", text="When unsure, deny everything to be safe")
    kw = dict(threshold=0.02, workers=2, runs_dir=runs_dir, playbook_dir=pb_dir)
    run_once(_claims(), _team, lambda f, p, h: [bad], **kw)   # run 1 proposes the overreach
    run_once(_claims(), _team, lambda f, p, h: [bad], **kw)   # run 2 rolls it back; supervisor repeats it
    state = load_state(runs_dir)
    assert state["pending_edits"] == [] and state["next_version"] == 0  # blocked, so run 3 re-runs the best config
    assert "already tried in run 2 and rolled back" in (runs_dir / "run_002" / "changes.md").read_text()


def test_summary_only_history_uses_labelled_current_failure_evidence(tmp_path):
    from triage import playbook as pbmod
    from triage.runs import load_state, run_once

    pb_dir, runs_dir = tmp_path / "playbook", tmp_path / "runs"
    pbmod.save(Playbook(), pb_dir)
    kw = dict(threshold=0.02, workers=2, runs_dir=runs_dir, playbook_dir=pb_dir)
    run_once(_claims(), _team, lambda f, p, h: [Edit(op="add", text="Deny when an excluded driver was driving", role="coverage")], **kw)
    run_once(_claims(), _team, lambda f, p, h: [Edit(op="instruct", role="coverage", text="When unsure, deny everything to be safe")], **kw)
    (runs_dir / "run_002" / "failures.json").unlink()
    observed = []

    def review(fails, base, history):
        observed.extend(fails)
        assert base.version == 1
        return []

    row = run_once(_claims(), _team, review, **kw)
    assert row["status"] == "rolled back"
    assert observed and all(f["measured_config_version"] == 2 and f["learning_base_version"] == 1 for f in observed)
    assert load_state(runs_dir)["best"]["version"] == 1
    assert (runs_dir / "run_003" / "summary.json").exists()
    import json
    assert len(json.loads((runs_dir / "run_003" / "predictions.json").read_text(encoding="utf-8"))) == 20


def test_unicode_model_output_roundtrips_in_csv_and_playbook(tmp_path):
    from triage import playbook as pbmod
    from triage.evaluate import write_results_csv
    from triage.scorer import Report, score_claim

    claim = _claims()[0]
    rationale = "A non\u2011breaking hyphen and \u65e5\u672c\u8a9e must survive export."
    prediction = NAIVE.model_copy(update={"rationale": rationale})
    report = Report([score_claim(claim.id, prediction, claim.gold)], {claim.id: prediction}, {claim.id: claim.gold})
    path = write_results_csv([claim], report, tmp_path / "results.csv")
    assert rationale in path.read_text(encoding="utf-8")
    pbmod.save(Playbook(version=1, role_instructions={"decider": rationale}), tmp_path / "playbook")
    assert pbmod.load(1, tmp_path / "playbook").role_instructions["decider"] == rationale
