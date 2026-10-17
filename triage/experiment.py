"""A complete, auditable improvement experiment with separate training and benchmark files."""

import json
from pathlib import Path

from triage.agents import TriageFn
from triage.config import PLAYBOOK_DIR
from triage.dataset import Claim
from triage.evaluate import evaluate, write_outputs
from triage.llm import USAGE
from triage.loop import improve
from triage.playbook import Playbook
from triage.scorer import Report, WEIGHTS
from triage.supervisor import ProposeFn
from triage.schemas import TriageDecision


def validate_partitions(partitions: dict[str, list[Claim]]) -> None:
    ids: set[str] = set()
    descriptions: set[str] = set()
    for name, claims in partitions.items():
        if not claims:
            raise ValueError(f"{name} dataset is empty")
        for claim in claims:
            description = " ".join(claim.description.lower().split())
            if claim.id in ids or description in descriptions:
                raise ValueError(f"duplicate claim ID or description in {name}: {claim.id}")
            if claim.gold is None:
                raise ValueError(f"missing structured gold for {claim.id}")
            ids.add(claim.id)
            descriptions.add(description)


def _comparison(before: Report, after: Report) -> dict:
    before_scores = {s.claim_id: s for s in before.scores}
    deltas = [s.score - before_scores[s.claim_id].score for s in after.scores]
    gain = after.cost_weighted_score - before.cost_weighted_score
    safe = after.wrongful_denials <= before.wrongful_denials and after.errors == 0
    return {
        "before": before.summary(),
        "after": after.summary(),
        "score_gain": round(gain, 6),
        "score_improved": gain > 1e-9,
        "safety_non_regression": safe,
        "observed_improvement": gain > 1e-9 and safe,
        "zero_wrongful_denials": after.wrongful_denials == 0,
        "claims_improved": sum(d > 1e-9 for d in deltas),
        "claims_worsened": sum(d < -1e-9 for d in deltas),
        "claims_unchanged": sum(abs(d) <= 1e-9 for d in deltas),
    }


def run_experiment(train: list[Claim], validation: list[Claim], hidden_test: list[Claim], start: Playbook,
                   triage_fn: TriageFn, propose_fn: ProposeFn, *, report_dir: Path, runs_dir: Path,
                   cycles: int = 3, threshold: float = 0.01, workers: int = 8,
                   playbook_dir: Path = PLAYBOOK_DIR, configuration: dict | None = None,
                   resume_baseline: Path | None = None, echo=print) -> dict:
    partitions = {"train": train, "validation": validation, "hidden_test": hidden_test}
    validate_partitions(partitions)
    report_dir.mkdir(parents=True, exist_ok=False)
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "status": "running", "phase": "baseline", "initial_version": start.version,
        "max_cycles": cycles, "threshold": threshold, "workers": workers,
        "claim_ids": {name: [c.id for c in claims] for name, claims in partitions.items()},
        "configuration": configuration or {}, "score_weights": WEIGHTS,
    }
    if resume_baseline is not None:
        original = json.loads((resume_baseline / "experiment.json").read_text(encoding="utf-8"))
        if original["configuration"] != manifest["configuration"] or original["claim_ids"] != manifest["claim_ids"]:
            raise ValueError("cannot reuse a baseline with different datasets, models, or configuration")
        if original["initial_version"] != start.version or original["score_weights"] != WEIGHTS:
            raise ValueError("cannot reuse a baseline with a different playbook or scoring weights")
        manifest["resumed_baseline_from"] = str(resume_baseline.resolve())

    def checkpoint(phase):
        manifest["phase"] = phase
        (report_dir / "experiment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checkpoint("baseline")
    before: dict[str, Report] = {}
    for name in ("train", "validation"):
        echo(f"Baseline v{start.version}: evaluating all {len(partitions[name])} {name} claims")
        cached = {}
        if resume_baseline is not None:
            saved_path = resume_baseline / f"v{start.version}_{name}.json"
            if saved_path.exists():
                saved = json.loads(saved_path.read_text(encoding="utf-8"))
                cached = {row["claim_id"]: TriageDecision.model_validate(row["decision"])
                          for row in saved["claims"] if "agent_error" not in row["mistakes"]}
                echo(f"  Reusing {len(cached)} successful baseline decisions; retrying incomplete claims only")

        def baseline_team(claim, playbook):
            return cached[claim.id] if claim.id in cached else triage_fn(claim, playbook)

        before[name] = evaluate(partitions[name], start, baseline_team, workers)
        write_outputs(partitions[name], before[name], start, name, runs_dir=runs_dir, reports_dir=report_dir)
        echo(f"  {name}: {before[name].summary()}")
        if before[name].errors:
            manifest["status"] = "failed"
            checkpoint(f"baseline {name} contains model errors; fix these before optimization")
            raise RuntimeError(f"baseline {name} contains {before[name].errors} model error(s)")

    checkpoint("improvement")
    result = improve(train, validation, start, triage_fn, propose_fn, cycles=cycles,
                     threshold=threshold, workers=workers, playbook_dir=playbook_dir, echo=echo,
                     initial_train_report=before["train"], initial_val_report=before["validation"],
                     report_dir=report_dir)
    final = result.final
    manifest["final_version"] = final.version
    checkpoint("final playbook frozen; comparing training and untouched hidden test")
    after: dict[str, Report] = {"validation": result.validation_report}
    if final.version == start.version:
        after["train"] = before["train"]
    else:
        echo(f"Final v{final.version}: evaluating all {len(train)} original training claims")
        after["train"] = evaluate(train, final, triage_fn, workers)
    for name in ("train", "validation"):
        write_outputs(partitions[name], after[name], final, name, runs_dir=runs_dir, reports_dir=report_dir)

    echo(f"Final playbook is frozen. Evaluating v{start.version} on {len(hidden_test)} hidden test claims")
    before["hidden_test"] = evaluate(hidden_test, start, triage_fn, workers)
    write_outputs(hidden_test, before["hidden_test"], start, "hidden_test",
                  runs_dir=runs_dir, reports_dir=report_dir)
    if final.version == start.version:
        after["hidden_test"] = before["hidden_test"]
    else:
        echo(f"Evaluating v{final.version} on the same hidden test claims")
        after["hidden_test"] = evaluate(hidden_test, final, triage_fn, workers)
        write_outputs(hidden_test, after["hidden_test"], final, "hidden_test",
                      runs_dir=runs_dir, reports_dir=report_dir)

    manifest.update({
        "status": "complete", "phase": "complete",
        "comparisons": {name: _comparison(before[name], after[name]) for name in partitions},
        "validation_history": result.val_history,
        "accepted_edits": [d.__dict__ for d in result.log if d.accepted],
        "candidates_evaluated": sum(d.val_score is not None for d in result.log),
        "usage": USAGE.snapshot(),
        "limitations": [
            "Validation and hidden test are synthetic fixtures based on the demo rules, not independently adjudicated real claims.",
            "Training gold was model-extracted from supplied expected outcomes; conditional and unspecified labels remain.",
            "Each candidate and each final comparison is evaluated once; model variability and statistical significance are not established.",
            "The acceptance gate prevents an increase in validation wrongful denials; it does not require zero.",
            "The supervisor receives current training failures and the current playbook, not a memory of all rejected edits or complete intermediate agent traces.",
        ],
    })
    checkpoint("complete")
    _write_summary(manifest, result.log, report_dir)
    echo(f"Experiment complete: {report_dir / 'summary.md'}")
    return manifest


def _write_summary(manifest: dict, decisions: list, directory: Path) -> None:
    start, final = manifest["initial_version"], manifest["final_version"]
    comparisons = manifest["comparisons"]
    hidden = comparisons["hidden_test"]
    outcome = ("Observed improvement on the separate hidden test in this run."
               if hidden["observed_improvement"] else
               "Improvement on the separate hidden test was not demonstrated in this run.")
    lines = ["# End-to-end improvement experiment", "", outcome, "",
             f"Started with v{start}; finished with v{final}. All original 50 rows were used for training." if
             len(manifest["claim_ids"]["train"]) == 50 else f"Started with v{start}; finished with v{final}.", "",
             "Training, validation, and hidden test come from separate files. Hidden test decisions were evaluated only after the final playbook was frozen.", "",
             f"The loop evaluated {manifest['candidates_evaluated']} candidates and saved {len(manifest['accepted_edits'])} edits.", "",
             "| Dataset | Claims | Cost-weighted before | After | Gain (percentage points) | Exact match before | After | Wrongful denials before | After | Model errors before / after |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, comparison in comparisons.items():
        b, a = comparison["before"], comparison["after"]
        lines.append(f"| {name} | {b['claims']} | {b['cost_weighted_score']:.1%} | {a['cost_weighted_score']:.1%} | "
                     f"{comparison['score_gain'] * 100:+.2f} | {b['exact_match']:.1%} | {a['exact_match']:.1%} | "
                     f"{b['wrongful_denials']} | {a['wrongful_denials']} | {b['errors']} / {a['errors']} |")
    lines += ["", "The cost-weighted score is a normalized penalty score, not accuracy. Training improvement alone does not establish generalization.", "",
              "## Candidate decisions", "", "| Cycle | Selected | Eligible | Validation score | Reason | Edit |",
              "|---|---|---|---:|---|---|"]
    for decision in decisions:
        score = f"{decision.val_score:.1%}" if decision.val_score is not None else "not evaluated"
        edit = decision.edit.replace("|", "\\|").replace("\n", " ")
        reason = decision.reason.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {decision.cycle} | {decision.accepted} | {decision.eligible} | {score} | {reason} | {edit} |")
    lines += ["", "## Files", "",
              "- [Complete results and frozen dataset IDs](experiment.json)",
              "- [Candidate proposals and selection log](improvement_log.json)",
              f"- [Original 50-claim results](v{start}_train.json)",
              f"- [Final 50-claim results](v{final}_train.json)",
              f"- [Original hidden test results](v{start}_hidden_test.json)",
              f"- [Final hidden test results](v{final}_hidden_test.json)", "",
              "CSV results are in the matching experiment folder under runs/. Learned playbooks are saved under playbook/.", "",
              "## Limits and remaining logic", ""]
    lines += [f"- {limitation}" for limitation in manifest["limitations"]]
    lines += ["", f"Total experiment inference usage: `{manifest['usage']}`.", ""]
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
