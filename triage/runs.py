"""Run-by-run improvement on the full dataset.

Each run: score all claims with the current config -> keep it if it beats the best run by `threshold`,
otherwise roll back to the best -> the supervisor proposes edits on top of the best config -> those edits
become the config the next run measures.
"""

import csv
import json
import logging
import time
from collections import Counter
from pathlib import Path

from triage import guardrails, playbook as pbmod
from triage.agents import TriageFn
from triage.config import PLAYBOOK_DIR, REPORTS_DIR, RUNS_DIR, WORKERS
from triage.dataset import Claim
from triage.evaluate import evaluate, write_results_csv
from triage.llm import USAGE
from triage.log import banner, log_to_file, section
from triage.supervisor import ProposeFn, apply_edit, failures

log = logging.getLogger("triage.run")

HISTORY_FIELDS = ["run", "config_version", "avg_score", "exact_match", "wrongful_denials", "status",
                  "best_version_after", "changes_tested", "errors", "calls", "input_tokens", "output_tokens"]


def _empty_state() -> dict:
    return {"runs": 0, "best": None, "next_version": 0, "pending_edits": [], "edit_history": []}


def load_state(runs_dir: Path = RUNS_DIR) -> dict:
    p = runs_dir / "state.json"
    return json.loads(p.read_text()) if p.exists() else _empty_state()


def _usage_delta(before: dict) -> dict:
    now = USAGE.snapshot()
    return {k: now[k] - before[k] for k in now}


def run_once(claims: list[Claim], triage_fn: TriageFn, propose_fn: ProposeFn, threshold: float = 0.02,
             workers: int = WORKERS, runs_dir: Path = RUNS_DIR, playbook_dir: Path = PLAYBOOK_DIR) -> dict:
    state = load_state(runs_dir)
    run_no = state["runs"] + 1
    run_dir = runs_dir / f"run_{run_no:03d}"
    with log_to_file(run_dir / "run.log"):
        row = _run(claims, triage_fn, propose_fn, threshold, workers, runs_dir, playbook_dir, state, run_no, run_dir)
    log.info("run %d files: %s (run.log has the full debug trace)", run_no, run_dir)
    return row


def _run(claims, triage_fn, propose_fn, threshold, workers, runs_dir, playbook_dir, state, run_no, run_dir) -> dict:
    usage_before = USAGE.snapshot()
    started = time.perf_counter()
    config = pbmod.load(state["next_version"], playbook_dir)
    tested: list[dict] = state["pending_edits"]
    best = state["best"]

    best_txt = "none" if best is None else "run {run} v{version} ({score:.3f})".format(**best)
    banner(log, f"RUN {run_no}  |  config v{config.version}  |  {len(claims)} claims  |  best so far: {best_txt}")
    log.info("config v%d: %d active rules, prompt add-ons for %s", config.version, len(config.active()),
             ", ".join(config.role_instructions) or "no roles")
    if tested:
        log.info("this run tests %d edit(s) proposed after run %d:", len(tested), run_no - 1)
        for t in tested:
            log.info("  + %s", t["edit"])
    else:
        log.info("this run tests: nothing new (baseline or re-run of the best config)")

    # 1. Measure the current config on every claim.
    section(log, "Phase 1/3  score every claim")
    report = evaluate(claims, config, triage_fn, workers)
    summary = report.summary()
    score = report.cost_weighted_score
    fails = failures(claims, report)
    _log_scorecard(report, summary, runs_dir, best)

    # 2. Keep it or roll back. Wrongful denials may never rise above the best run's.
    section(log, "Phase 2/3  keep or roll back")
    if best is None:
        status, why = "baseline", "first run"
    elif summary["wrongful_denials"] > best["wrongful_denials"]:
        status, why = "rolled back", f"wrongful denials rose {best['wrongful_denials']} -> {summary['wrongful_denials']}"
    elif score >= best["score"] + threshold:
        status, why = "kept", f"score {score:.3f} beats best {best['score']:.3f} by {score - best['score']:+.3f} (>= {threshold})"
    else:
        status, why = "rolled back", f"score {score:.3f} vs best {best['score']:.3f} ({score - best['score']:+.3f} < {threshold})"
    if tested:
        state["edit_history"].append({"run": run_no, "config_version": config.version, "edits": tested,
                                      "result": status, "score": round(score, 3),
                                      "best_score_before": round(best["score"], 3) if best else None})
    if status != "rolled back":
        best = {"run": run_no, "version": config.version, "score": score,
                "wrongful_denials": summary["wrongful_denials"]}
    log.info("%s: %s", status.upper(), why)
    log.info("best config is now v%d from run %d (%.3f)", best["version"], best["run"], best["score"])

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.yaml").write_text(pbmod.dump(config))
    write_results_csv(claims, report, run_dir / "results.csv")
    (run_dir / "failures.json").write_text(json.dumps(fails, indent=1))

    # 3. Propose the next config on top of the best one, learning from the best run's mistakes.
    section(log, "Phase 3/3  supervisor proposes the next config")
    base = pbmod.load(best["version"], playbook_dir)
    base_fails = fails if best["run"] == run_no else json.loads(
        (runs_dir / f"run_{best['run']:03d}" / "failures.json").read_text())
    proposed, rejected = [], []
    if base_fails:
        log.info("asking the supervisor: %d claims with mistakes in run %d, %d past edit batches in history",
                 len(base_fails), best["run"], len(state["edit_history"]))
        t0 = time.perf_counter()
        edits = propose_fn(base_fails, base, state["edit_history"])
        log.info("supervisor replied with %d edit(s) in %.0fs", len(edits), time.perf_counter() - t0)
        descriptions = [c.description for c in claims]
        candidate = base
        next_version = pbmod.latest_version(playbook_dir) + 1
        for edit in edits:
            issues = guardrails.lint_rule(edit.text, descriptions)
            if not issues:
                try:
                    candidate = apply_edit(candidate, edit, next_version)
                except ValueError as e:
                    issues = [str(e)]
            (rejected if issues else proposed).append({"edit": edit.describe(), "issues": issues,
                                                       "rationale": edit.rationale,
                                                       "source_claim_ids": edit.source_claim_ids})
            if issues:
                log.warning("  ✗ rejected  %s", edit.describe())
                log.warning("              reason: %s", "; ".join(issues))
            else:
                log.info("  ✓ accepted  %s", edit.describe())
                log.info("              why: %s (claims %s)", edit.rationale or "-", ", ".join(edit.source_claim_ids) or "-")
        if proposed:
            candidate.changelog.append(f"v{next_version}: proposed after run {run_no} on top of v{base.version}: "
                                       + " | ".join(p["edit"] for p in proposed))
            pbmod.save(candidate, playbook_dir)
            state["next_version"] = next_version
            log.info("saved config v%d (v%d + %d edit(s)); run %d will measure it", next_version, base.version,
                     len(proposed), run_no + 1)
        else:
            state["next_version"] = base.version
            log.info("no usable edits; run %d will re-run config v%d", run_no + 1, base.version)
    else:
        state["next_version"] = base.version
        log.info("no mistakes left; nothing to propose")

    usage = _usage_delta(usage_before)
    summary.update(run=run_no, config_version=config.version, status=status, best=best, usage=usage)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (run_dir / "changes.md").write_text(_changes_md(run_no, config.version, status, tested, best, summary,
                                                    proposed, rejected, state["next_version"]))

    state.update(runs=run_no, best=best, pending_edits=proposed)
    (runs_dir / "state.json").write_text(json.dumps(state, indent=2))
    row = {
        "run": run_no, "config_version": config.version, "avg_score": round(score, 3),
        "exact_match": summary["exact_match"], "wrongful_denials": summary["wrongful_denials"], "status": status,
        "best_version_after": best["version"], "changes_tested": " ; ".join(t["edit"] for t in tested),
        "errors": summary["errors"], **usage,
    }
    history = runs_dir / "history.csv"
    new_file = not history.exists()
    with history.open("a", newline="") as f:
        w = csv.DictWriter(f, HISTORY_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    banner(log, f"RUN {run_no} DONE  |  {status}  |  avg {score:.3f}  |  {time.perf_counter() - started:.0f}s  |  "
                f"{usage['calls']} Crusoe calls, {usage['input_tokens']:,} in / {usage['output_tokens']:,} out tokens")
    return row


def _log_scorecard(report, summary: dict, runs_dir: Path, best: dict | None) -> None:
    """Scores vs the best run so far, per category, and which mistakes happened."""
    best_cats = {}
    if best:
        p = runs_dir / f"run_{best['run']:03d}" / "summary.json"
        if p.exists():
            best_cats = json.loads(p.read_text())["by_category"]
    log.info("%-18s %7s %7s", "category", "now", "best")
    for cat, val in summary["by_category"].items():
        prev = best_cats.get(cat)
        arrow = "" if prev is None else ("  ▲" if val > prev else "  ▼" if val < prev else "")
        log.info("%-18s %7.3f %7s%s", cat, val, "-" if prev is None else f"{prev:.3f}", arrow)
    counts = Counter(m for s in report.scores for m in s.mistakes)
    if counts:
        log.info("mistakes: %s", ", ".join(f"{k} x{v}" for k, v in counts.most_common()))
    log.info("avg %.3f | exact %s | wrongful denials %d | straight-through %s | SIU hit %s | subrogation caught %s",
             summary["cost_weighted_score"], summary["exact_match"], summary["wrongful_denials"],
             summary["straight_through_rate"], summary["siu_hit_rate"], summary["subrogation_caught"])


def _changes_md(run_no, version, status, tested, best, summary, proposed, rejected, next_version) -> str:
    lines = [f"# Run {run_no}: config v{version} — {status}", "",
             f"- Avg score **{summary['cost_weighted_score']}**, exact match {summary['exact_match']}, "
             f"wrongful denials {summary['wrongful_denials']}",
             f"- Best after this run: run {best['run']} (v{best['version']}, {best['score']:.3f})", "",
             "## Changes this run tested", ""]
    lines += [f"- {t['edit']} — {t['rationale']}" for t in tested] or ["- none (baseline or re-run)"]
    lines += ["", f"## Proposed for the next run (config v{next_version})", ""]
    lines += [f"- {p['edit']} — {p['rationale']} (from claims {p['source_claim_ids']})" for p in proposed] or ["- none"]
    if rejected:
        lines += ["", "## Rejected by the linter", ""]
        lines += [f"- {r['edit']} — {'; '.join(r['issues'])}" for r in rejected]
    lines += ["", "## Score by category (gold route)", "", "| Category | Score |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary["by_category"].items()]
    return "\n".join(lines) + "\n"


def read_history(runs_dir: Path = RUNS_DIR) -> list[dict]:
    p = runs_dir / "history.csv"
    return list(csv.DictReader(p.open())) if p.exists() else []


def history_report(runs_dir: Path = RUNS_DIR, out: Path = REPORTS_DIR / "history.md") -> str:
    rows = read_history(runs_dir)
    if not rows:
        return "No runs yet. Start with: uv run triage run"
    runs = [r["run"] for r in rows]
    avg = [float(r["avg_score"]) for r in rows]
    exact = [float(r["exact_match"] or 0) for r in rows]
    lines = ["# Improvement over runs", "", f"All {len(rows)} runs score the same full dataset.", "",
             "```mermaid", "xychart-beta", '    title "Score per run"', f"    x-axis [{', '.join(runs)}]",
             '    y-axis "score" 0 --> 1', f"    line [{', '.join(map(str, avg))}]",
             f"    line [{', '.join(map(str, exact))}]", "```", "",
             "Lines: average cost-weighted score (first), exact-match rate (second).", "",
             "| Run | Config | Avg score | Exact match | Wrongful denials | Status | Changes tested |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['run']} | v{r['config_version']} | {r['avg_score']} | {r['exact_match']} | "
              f"{r['wrongful_denials']} | {r['status']} | {r['changes_tested'] or '—'} |" for r in rows]

    cats: dict[str, dict[str, float]] = {}
    for r in rows:
        s = runs_dir / f"run_{int(r['run']):03d}" / "summary.json"
        if s.exists():
            for k, v in json.loads(s.read_text())["by_category"].items():
                cats.setdefault(k, {})[r["run"]] = v
    if cats:
        lines += ["", "## Score by category (gold route)", "",
                  "| Category | " + " | ".join(f"run {x}" for x in runs) + " |",
                  "|---|" + "---|" * len(runs)]
        lines += [f"| {k} | " + " | ".join(str(v.get(x, "—")) for x in runs) + " |" for k, v in sorted(cats.items())]
    text = "\n".join(lines) + "\n"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    return text
