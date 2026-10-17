"""Run the team over a set of claims, score against gold, and write reports."""

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from triage.agents import TriageFn
from triage.config import REPORTS_DIR, RUNS_DIR, WORKERS
from triage.dataset import Claim
from triage.llm import USAGE
from triage.playbook import Playbook
from triage.schemas import TriageDecision
from triage.scorer import MAX_PENALTY, Report, score_claim

ERROR_DECISION = TriageDecision(coverage="coverage_review", route="coverage_review", human_signoff=True,
                                rationale="agent error")


def evaluate(claims: list[Claim], playbook: Playbook, triage_fn: TriageFn, workers: int = WORKERS) -> Report:
    errors: list[str] = []

    def run(claim: Claim) -> TriageDecision:
        try:
            return triage_fn(claim, playbook)
        except Exception as e:  # one bad reply should not sink a whole evaluation run
            errors.append(claim.id)
            print(f"[error] claim {claim.id}: {type(e).__name__}: {e}", file=sys.stderr)
            return ERROR_DECISION

    with ThreadPoolExecutor(max(1, workers)) as pool:
        preds = dict(zip((c.id for c in claims), pool.map(run, claims)))
    scores = [score_claim(c.id, preds[c.id], c.gold) for c in claims]
    for score in scores:
        if score.claim_id in errors:
            score.mistakes.append("agent_error")
            score.penalty = max(score.penalty, MAX_PENALTY)
    return Report(scores, preds, {c.id: c.gold for c in claims}, len(errors))


def write_outputs(claims: list[Claim], report: Report, playbook: Playbook, split: str, *,
                  runs_dir: Path = RUNS_DIR, reports_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    runs_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    scores = {s.claim_id: s for s in report.scores}

    csv_path = runs_dir / f"dataset_scored_v{playbook.version}_{split}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["s.no", "state", "description", "expected_outcome_gold", "agent_outcome", "evaluation", "mistakes"])
        for c in claims:
            d, s = report.preds[c.id], scores[c.id]
            w.writerow([c.id, c.state, c.description, c.gold_text, f"{d.brief()}. {d.rationale}",
                        f"{s.score:.0%}", ";".join(s.mistakes)])

    json_path = reports_dir / f"v{playbook.version}_{split}.json"
    json_path.write_text(json.dumps({
        "playbook_version": playbook.version,
        "split": split,
        "summary": report.summary(),
        "usage": USAGE.snapshot(),
        "claims": [{"claim_id": s.claim_id, "score": s.score, "mistakes": s.mistakes,
                    "decision": report.preds[s.claim_id].model_dump()} for s in report.scores],
    }, indent=2), encoding="utf-8")
    return csv_path, json_path
