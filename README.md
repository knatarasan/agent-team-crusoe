# Version N+1: a self-improving claims triage team on Crusoe

A LangGraph agent team (Intake → Coverage → Liability → Fraud → Decider) triages rear-end auto claims. After every run over the whole dataset, a Claims Supervisor studies the mistakes and edits the team's config: plain-English house rules plus per-role prompt add-ons. The next run measures those edits. They're kept if the score improves and rolled back if not. See [plan.md](plan.md) for the business case and [architecture.md](architecture.md) for the design.

## Setup

```bash
uv sync
cp .env.example .env        # add CRUSOE_API_KEY
uv run triage ping          # lists models your key can use and says hello to both
uv run triage prepare-gold  # free-text gold -> data/gold_structured.jsonl (review it!)
```

## Run

```bash
uv run triage run              # run 1: baseline with config v0, then proposes v1
uv run triage run --times 3    # runs 2-4: each measures the previous proposal, keeps or rolls back
uv run triage -v run           # also log every agent's output and each Crusoe call (latency, tokens)
uv run triage history          # run-by-run table + chart -> reports/history.md
uv run triage eval --version 2 # score any config on all claims, no learning
uv run pytest                  # offline tests (no Crusoe calls)
```

Options for `run`: `--k 3` caps the edits proposed per run, and `--threshold 0.02` sets the score gain over the best run needed to keep a config.

To start over, delete `runs/` and every `playbook/vN.yaml` except `v0.yaml`.

## What each run writes

```
runs/
  run_001/  config.yaml    the config this run measured
            results.csv    all claims with agent_outcome + evaluation filled in
            summary.json   scores, per-category, KPIs, Crusoe token usage
            changes.md     what was tested, kept or rolled back, what's proposed next
            failures.json  the mistakes the supervisor learns from
  history.csv              one row per run: the improvement curve
  state.json               best run so far, next config, edit history
playbook/vN.yaml           every proposed config (rules + role prompt add-ons)
reports/history.md         chart of avg score and exact match per run
```

## Layout

| Path | What |
|---|---|
| `triage/llm.py` | Crusoe client (`ChatOpenAI` → Crusoe base URL), JSON parsing, token usage |
| `triage/agents.py` | LangGraph team and fixed day-1 base prompts |
| `triage/playbook.py` | Versioned config: rules + role prompt add-ons |
| `triage/guardrails.py` | Sign-off on denials, injury never fast-tracked, rule linter |
| `triage/dataset.py`, `triage/gold.py` | CSV loading, structured gold cache |
| `triage/scorer.py` | Cost-weighted score and KPIs |
| `triage/supervisor.py` | Proposes edits from mistakes and the edit history |
| `triage/runs.py` | Run → keep or roll back → propose next; history and chart |
| `triage/cli.py` | `ping`, `prepare-gold`, `run`, `history`, `eval` |
