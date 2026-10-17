import json
from collections import Counter
from pathlib import Path

import pytest

from triage.dataset import Claim, load_claims, load_labeled_claims
from triage.experiment import run_experiment, validate_partitions
from triage.loop import improve
from triage.playbook import Playbook
from triage.schemas import GoldDecision, TriageDecision
from triage.supervisor import Edit


NORMAL = GoldDecision(coverage="covered", fault_pct=100, pay_other_party=True, pay_own_vehicle=True,
                      deductible_applies=True, route="fast_track")
DENIED = GoldDecision(coverage="denied", route="deny", human_signoff=True)


def claims(prefix):
    return [Claim(f"{prefix}{i}", "CA", f"{prefix} incident {i}" + (" excluded" if i % 2 else " ordinary"),
                  "excluded driver" if i % 2 else "ordinary", DENIED if i % 2 else NORMAL) for i in range(4)]


def test_experiment_runs_full_training_and_keeps_hidden_feedback_out_of_learning(tmp_path):
    train, validation, hidden = claims("train"), claims("val"), claims("hidden")
    seen_train_ids = {c.id for c in train}
    playbooks = tmp_path / "playbooks"
    hidden_calls = []

    def team(claim, pb):
        if claim.id.startswith("hidden"):
            assert (playbooks / "v1.yaml").exists(), "hidden evaluation must follow final selection"
            hidden_calls.append((claim.id, pb.version))
        learned = any("actual excluded driver" in rule.text for rule in pb.active())
        return TriageDecision.model_validate((DENIED if learned and "excluded" in claim.description else NORMAL).model_dump())

    def propose(failures, pb):
        assert {f["claim_id"] for f in failures} <= seen_train_ids
        return [Edit(op="add", role="coverage", text="Check the actual excluded driver before paying")]

    result = run_experiment(train, validation, hidden, Playbook(), team, propose,
                            report_dir=tmp_path / "reports", runs_dir=tmp_path / "runs",
                            playbook_dir=playbooks, cycles=1, workers=2, echo=lambda *args: None)
    assert result["status"] == "complete" and result["final_version"] == 1
    assert all(c["observed_improvement"] for c in result["comparisons"].values())
    assert len(result["accepted_edits"]) == 1
    assert len(hidden_calls) == 8
    assert len(json.loads((tmp_path / "reports/v0_train.json").read_text())["claims"]) == 4
    assert (tmp_path / "reports/summary.md").exists()


def test_partitions_reject_duplicate_ids_or_descriptions():
    train = claims("train")
    with pytest.raises(ValueError, match="duplicate"):
        validate_partitions({"train": train, "validation": [train[0]]})
    duplicate = Claim("new-id", "TX", train[0].description.upper(), "ordinary", NORMAL)
    with pytest.raises(ValueError, match="duplicate"):
        validate_partitions({"train": train, "validation": [duplicate]})


def test_only_best_eligible_candidate_is_logged_as_accepted(tmp_path):
    def team(claim, pb):
        text = " ".join(r.text for r in pb.active())
        if "excluded" in claim.description:
            if "deny excluded" in text:
                return TriageDecision.model_validate(DENIED.model_dump())
            if "review excluded" in text:
                return TriageDecision(coverage="coverage_review", route="coverage_review", human_signoff=True)
        return TriageDecision.model_validate(NORMAL.model_dump())

    def propose(failures, pb):
        return [Edit(op="add", text="review excluded"), Edit(op="add", text="deny excluded")]

    result = improve(claims("train"), claims("val"), Playbook(), team, propose, cycles=1,
                     workers=1, playbook_dir=tmp_path, echo=lambda *args: None)
    assert sum(d.eligible for d in result.log) == 2
    assert sum(d.accepted for d in result.log) == 1
    assert result.final.rules[0].text == "deny excluded"
    saved = json.loads((tmp_path / "improvement_log.json").read_text())
    assert sum(d["accepted"] for d in saved["decisions"]) == 1


def test_candidate_execution_error_is_rejected(tmp_path):
    def team(claim, pb):
        if pb.active():
            raise ValueError("malformed candidate response")
        return TriageDecision.model_validate(NORMAL.model_dump())

    result = improve(claims("train"), claims("val"), Playbook(), team,
                     lambda fails, pb: [Edit(op="add", text="check policy")], cycles=1,
                     workers=1, playbook_dir=tmp_path, echo=lambda *args: None)
    assert result.final.version == 0
    assert not result.log[0].accepted
    assert "model execution error" in result.log[0].reason


def test_separate_benchmarks_are_balanced_and_do_not_duplicate_training():
    root = Path(__file__).resolve().parents[1]
    train = load_claims(root / "data/dataset.csv")
    validation = load_labeled_claims(root / "data/validation.jsonl")
    hidden = load_labeled_claims(root / "data/hidden_test.jsonl")
    all_claims = train + validation + hidden
    assert len({c.id for c in all_claims}) == len(all_claims)
    assert len({" ".join(c.description.lower().split()) for c in all_claims}) == len(all_claims)
    for dataset in (validation, hidden):
        assert len(dataset) == 24
        assert set(Counter(c.gold.route for c in dataset).values()) == {4}
        assert all(c.gold.human_signoff for c in dataset if c.gold.injury or c.gold.route in {"deny", "coverage_review", "adjuster", "siu"})


def test_rate_limit_backoff_retries_without_retrying_other_errors(monkeypatch):
    from langchain_core.messages import AIMessage
    from triage import llm as llmmod

    delays = []
    monkeypatch.setattr(llmmod.time, "sleep", delays.append)
    rate_error = type("OpenAIRateLimitError", (Exception,), {})

    class Model:
        calls = 0

        def invoke(self, msgs):
            self.calls += 1
            if self.calls < 3:
                raise rate_error("busy")
            return AIMessage('{"ok": true}')

    model = Model()
    assert llmmod.invoke_json(model, "system", "user") == {"ok": True}
    assert model.calls == 3 and delays == [15, 30]

    class Broken:
        def invoke(self, msgs):
            raise ValueError("not a rate limit")

    with pytest.raises(ValueError, match="not a rate limit"):
        llmmod.invoke_json(Broken(), "system", "user")
    assert delays == [15, 30]


def test_resuming_baseline_reuses_successes_and_retries_only_failed_claims(tmp_path):
    train, validation, hidden = claims("train"), claims("val"), claims("hidden")
    calls = []

    def team(claim, pb):
        calls.append(claim.id)
        return TriageDecision.model_validate(claim.gold.model_dump())

    first = tmp_path / "first"
    run_experiment(train, validation, hidden, Playbook(), team, lambda *args: [],
                   report_dir=first, runs_dir=tmp_path / "runs1", playbook_dir=tmp_path / "pb",
                   cycles=0, workers=1, echo=lambda *args: None)
    path = first / "v0_validation.json"
    saved = json.loads(path.read_text())
    saved["claims"][0]["mistakes"] = ["agent_error"]
    path.write_text(json.dumps(saved))
    calls.clear()
    run_experiment(train, validation, hidden, Playbook(), team, lambda *args: [],
                   report_dir=tmp_path / "second", runs_dir=tmp_path / "runs2", playbook_dir=tmp_path / "pb",
                   cycles=0, workers=1, resume_baseline=first, echo=lambda *args: None)
    assert calls == [validation[0].id] + [c.id for c in hidden]
