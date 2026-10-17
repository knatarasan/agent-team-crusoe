"""Run the team over a set of claims and score against gold."""

import csv
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from triage.agents import TriageFn
from triage.config import WORKERS
from triage.dataset import Claim
from triage.playbook import Playbook
from triage.schemas import TriageDecision
from triage.scorer import ClaimScore, Report, score_claim

log = logging.getLogger("triage.eval")

ERROR_DECISION = TriageDecision(coverage="coverage_review", route="coverage_review", human_signoff=True,
                                rationale="agent error")


def _progress_line(done: int, total: int, claim: Claim, pred: TriageDecision, s: ClaimScore, secs: float) -> str:
    mark = "✓" if not s.mistakes else "✗"
    gold = claim.gold
    line = f"[{done:>3}/{total}] {mark} #{claim.id:<4} {claim.state} {s.score:>4.0%}  {pred.coverage}/{pred.route}"
    if s.mistakes:
        line += f"  (gold {gold.coverage}/{gold.route})  {', '.join(s.mistakes)}"
    return f"{line}  {secs:.1f}s"


def evaluate(claims: list[Claim], playbook: Playbook, triage_fn: TriageFn, workers: int = WORKERS) -> Report:
    errors: list[str] = []
    preds: dict[str, TriageDecision] = {}
    scores: dict[str, ClaimScore] = {}
    lock = threading.Lock()
    started = time.perf_counter()
    log.info("scoring %d claims with config v%d (%d in parallel)", len(claims), playbook.version, max(1, workers))

    def run(claim: Claim) -> tuple[Claim, TriageDecision, float]:
        t0 = time.perf_counter()
        try:
            pred = triage_fn(claim, playbook)
        except Exception as e:  # one bad reply should not sink a whole run
            errors.append(claim.id)
            log.error("#%s failed: %s: %s", claim.id, type(e).__name__, e)
            pred = ERROR_DECISION
        return claim, pred, time.perf_counter() - t0

    with ThreadPoolExecutor(max(1, workers)) as pool:
        for fut in as_completed([pool.submit(run, c) for c in claims]):
            claim, pred, secs = fut.result()
            s = score_claim(claim.id, pred, claim.gold)
            with lock:
                preds[claim.id], scores[claim.id] = pred, s
                done = len(preds)
            log.info(_progress_line(done, len(claims), claim, pred, s, secs))

    report = Report([scores[c.id] for c in claims], {c.id: preds[c.id] for c in claims},
                    {c.id: c.gold for c in claims}, len(errors))
    log.info("scored %d claims in %.0fs: avg %.3f, exact %s, wrongful denials %d, errors %d", len(claims),
             time.perf_counter() - started, report.cost_weighted_score, report.summary()["exact_match"],
             report.wrongful_denials, len(errors))
    return report


def write_results_csv(claims: list[Claim], report: Report, path: Path) -> Path:
    """dataset.csv columns with agent_outcome and evaluation filled in, plus the mistakes found."""
    scores = {s.claim_id: s for s in report.scores}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["s.no", "state", "description", "expected_outcome_gold", "agent_outcome", "evaluation", "mistakes"])
        for c in claims:
            d, s = report.preds[c.id], scores[c.id]
            w.writerow([c.id, c.state, c.description, c.gold_text, f"{d.brief()}. {d.rationale}",
                        f"{s.score:.0%}", ";".join(s.mistakes)])
    return path
