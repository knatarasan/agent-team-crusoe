# Version N+1: a self-improving claims triage team on Crusoe

A LangGraph agent team (Intake → Coverage → Liability → Fraud → Decider) triages rear-end auto claims. A Claims Supervisor studies the team's mistakes and edits a plain-English playbook. An edit is kept only if it raises the cost-weighted score on validation claims. See [plan.md](plan.md) for the business case and [architecture.md](architecture.md) for the design.

## Setup

```bash
uv sync
cp .env.example .env        # add CRUSOE_API_KEY; confirm base URL and model ids
uv run triage ping          # lists models your key can use, says hello to both
```

## Run

```bash
uv run triage prepare-gold                  # free-text gold -> data/gold_structured.jsonl (review it!)
uv run triage eval --version 0 --split test # day-1 score; fills agent_outcome + evaluation in runs/*.csv
uv run triage improve --cycles 3 --k 3      # writes playbook/v1.yaml, v2.yaml ... + reports/improvement_log.json
uv run triage compare --before 0 --split test   # before/after table -> reports/before_after_test.md
uv run pytest                               # offline tests (no Crusoe calls)
```

With fewer than 30 rows in `data/dataset.csv`, train, validation and test are the same claims (smoke mode). The pipeline runs, but the before-and-after result isn't a held-out result until the dataset grows.

## Layout

| Path | What |
|---|---|
| `triage/llm.py` | Crusoe client (`ChatOpenAI` → Crusoe base URL), JSON parsing, token usage |
| `triage/agents.py` | LangGraph team and day-1 base prompts |
| `triage/playbook.py`, `playbook/vN.yaml` | Versioned rules; roll back by loading an older version |
| `triage/guardrails.py` | Sign-off on denials, injury never fast-tracked, rule linter |
| `triage/dataset.py`, `triage/gold.py` | CSV loading, structured gold cache, stratified splits |
| `triage/scorer.py` | Cost-weighted score and KPIs |
| `triage/supervisor.py`, `triage/loop.py` | Propose edits → validate → accept or reject |
| `triage/cli.py` | `ping`, `prepare-gold`, `eval`, `improve`, `compare` |
