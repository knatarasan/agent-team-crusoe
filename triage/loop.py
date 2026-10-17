"""The Version N+1 engine: run on train, propose edits, keep only those that improve validation."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from triage import guardrails, playbook as pbmod
from triage.agents import TriageFn
from triage.config import PLAYBOOK_DIR, REPORTS_DIR
from triage.dataset import Claim
from triage.evaluate import evaluate
from triage.playbook import Playbook
from triage.scorer import Report
from triage.supervisor import ProposeFn, apply_edit, failures


@dataclass
class Decision:
    cycle: int
    edit: str
    accepted: bool
    reason: str
    val_score: float | None = None
    gain: float | None = None
    eligible: bool = False
    edit_details: dict | None = None
    validation_summary: dict | None = None


@dataclass
class LoopResult:
    final: Playbook
    log: list[Decision] = field(default_factory=list)
    val_history: list[float] = field(default_factory=list)
    validation_report: Report | None = None


def improve(train: list[Claim], val: list[Claim], start: Playbook, triage_fn: TriageFn, propose_fn: ProposeFn,
            cycles: int = 3, threshold: float = 0.01, patience: int = 2, workers: int = 8,
            playbook_dir: Path = PLAYBOOK_DIR, echo=print, *,
            initial_train_report: Report | None = None, initial_val_report: Report | None = None,
            report_dir: Path | None = None) -> LoopResult:
    pb = start
    base = initial_val_report if initial_val_report is not None else evaluate(val, pb, triage_fn, workers)
    result = LoopResult(pb, val_history=[base.cost_weighted_score], validation_report=base)
    echo(f"v{pb.version} validation score {base.cost_weighted_score:.3f}")
    _write_log(result, playbook_dir, report_dir)
    stale = 0

    for cycle in range(1, cycles + 1):
        train_report = (initial_train_report if cycle == 1 and initial_train_report is not None
                        else evaluate(train, pb, triage_fn, workers))
        fails = failures(train, train_report)
        echo(f"cycle {cycle}: train score {train_report.cost_weighted_score:.3f}, {len(fails)} claims with mistakes")
        if not fails:
            echo("no mistakes left on train; stopping")
            break

        new_version = pbmod.latest_version(playbook_dir) + 1
        best = None
        for edit in propose_fn(fails, pb):
            issues = guardrails.lint_rule(edit.text)
            if issues:
                result.log.append(Decision(cycle, edit.describe(), False, "; ".join(issues),
                                           edit_details=edit.model_dump()))
                echo(f"  rejected (linter): {edit.describe()}")
                continue
            try:
                candidate = apply_edit(pb, edit, new_version)
            except ValueError as e:
                result.log.append(Decision(cycle, edit.describe(), False, str(e), edit_details=edit.model_dump()))
                continue
            report = evaluate(val, candidate, triage_fn, workers)
            gain = report.cost_weighted_score - base.cost_weighted_score
            if report.errors:
                ok, reason = False, f"candidate has {report.errors} model execution error(s)"
            elif report.wrongful_denials > base.wrongful_denials:
                ok, reason = False, f"wrongful denials {base.wrongful_denials} -> {report.wrongful_denials}"
            elif gain < threshold:
                ok, reason = False, f"validation gain {gain:+.3f} below threshold {threshold}"
            else:
                ok, reason = True, f"validation gain {gain:+.3f}"
            result.log.append(Decision(cycle, edit.describe(), False, reason, report.cost_weighted_score, gain,
                                       eligible=ok, edit_details=edit.model_dump(), validation_summary=report.summary()))
            _write_log(result, playbook_dir, report_dir)
            echo(f"  {'candidate' if ok else 'rejected'}: {edit.describe()} ({reason})")
            if ok and (best is None or gain > best[2]):
                best = (edit, candidate, gain, report, len(result.log) - 1)

        if best is None:
            stale += 1
            echo(f"cycle {cycle}: no edit accepted")
            _write_log(result, playbook_dir, report_dir)
            if stale >= patience:
                echo("no gain for consecutive cycles; stopping")
                break
            continue

        edit, candidate, gain, report, log_index = best
        result.log[log_index].accepted = True
        for r in candidate.rules:
            if r.added_in == new_version and r.score_delta is None:
                r.score_delta = round(gain, 4)
        candidate.changelog.append(f"v{new_version}: {edit.describe()} | val {gain:+.3f} | from claims {edit.source_claim_ids}")
        pbmod.save(candidate, playbook_dir)
        echo(f"cycle {cycle}: accepted -> v{new_version} (val {report.cost_weighted_score:.3f})")
        pb, base, stale = candidate, report, 0
        result.final = pb
        result.validation_report = report
        result.val_history.append(report.cost_weighted_score)
        _write_log(result, playbook_dir, report_dir)

    _write_log(result, playbook_dir, report_dir)
    return result


def _write_log(result: LoopResult, playbook_dir: Path, report_dir: Path | None = None) -> None:
    out_dir = report_dir if report_dir is not None else (REPORTS_DIR if playbook_dir == PLAYBOOK_DIR else playbook_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "improvement_log.json").write_text(json.dumps(
        {"final_version": result.final.version, "val_history": result.val_history,
         "decisions": [d.__dict__ for d in result.log]}, indent=2), encoding="utf-8")
