# Architecture: Version N+1 motor claims triage team

Status: **basic version built** (`triage/` package; see [README.md](README.md)). The gold answers come from `data/dataset.csv` (adjusters' decisions), not from an Oracle. The open decisions are listed at the end.

**Goal:** an agent team on Crusoe Managed Inference that triages rear-end auto claims (see [plan.md](plan.md)). After each run it reviews its mistakes against adjusters' decisions and rewrites its own playbook. It keeps a change only if a measured score improves, and it proves the gain with a before-and-after score on claims it has never seen.

## 1. System overview

```mermaid
flowchart TB
    subgraph Crusoe["Crusoe Managed Inference (OpenAI-compatible API)"]
        WM["Worker model<br/>(e.g. Llama-3.3-70B / gpt-oss-120b)"]
        OM["Optimizer model<br/>(e.g. DeepSeek-V3)"]
    end

    subgraph Data["Data"]
        CSV[("dataset.csv<br/>claims + adjuster gold text")]
        ORA["Gold extractor<br/>(free text → structured, cached)"]
        SPL[("train / val / test<br/>stratified by gold route")]
    end

    subgraph Team["Triage team (LangGraph)"]
        AG["Intake → Coverage → Liability → Fraud → Decider"]
        GR["Guardrails (code)"]
    end

    PB[("Playbook vN<br/>YAML, git-versioned")]
    SC["Scorer<br/>(cost-weighted vs gold)"]
    SUP["Claims Supervisor<br/>(reflector / optimizer)"]
    REP["Reports<br/>scorecards, changelog, cost"]

    CSV --> ORA --> SPL
    SPL -->|claim text| AG
    PB -->|rules by role + state| AG
    AG --> GR --> SC
    SPL -->|gold| SC
    SC -->|failures + traces| SUP
    SUP -->|candidate edits| PB
    SC --> REP
    AG -.calls.-> WM
    SUP -.calls.-> OM
    ORA -.one-time extraction.-> OM
```

## 2. Triage team (one claim)

Each agent's prompt is **base prompt + the playbook rules for its role and the claim's state**. Every agent cites the rule IDs it used.

```mermaid
flowchart LR
    FNOL["FNOL text<br/>+ policy"] --> I["Intake clerk<br/>extract facts"]
    I --> C["Coverage analyst<br/>policy, drivers, dates, use"]
    C -->|denied / review| D
    C -->|covered| L["Liability adjuster<br/>fault % (CA vs TX)"]
    L --> F["Fraud screener<br/>staging signals"]
    F --> D["Decider<br/>route + payments"]
    D --> G{"Guardrails"}
    G --> OUT["TriageDecision<br/>(structured)"]

    G -. "denial → human sign-off<br/>injury → never fast-track" .- G
```

**Output (`TriageDecision`):** coverage · fault % · pay other party · pay own vehicle · deductible applies · route (fast-track / adjuster / SIU / subrogation / coverage review / deny) · SIU referral · subrogation · human sign-off · rationale · rules cited.

## 3. Improvement loop (the Version N+1 engine)

```mermaid
flowchart TB
    START(["Playbook vN"]) --> RUN["Run team on TRAIN split"]
    RUN --> SCORE["Score vs gold<br/>collect failures + traces"]
    SCORE --> REFLECT["Claims Supervisor groups failures<br/>by root cause"]
    REFLECT --> PROPOSE["Propose K edits<br/>add / edit / retire rule,<br/>edit prompt, change topology"]
    PROPOSE --> LINT{"Rule linter<br/>no ZIP / protected attrs"}
    LINT -->|fail| DROP["Reject + log reason"]
    LINT -->|pass| VAL["Score each candidate<br/>on VALIDATION split"]
    VAL --> GATE{"Score ↑ by threshold?<br/>Wrongful denials not ↑?"}
    GATE -->|no| DROP
    GATE -->|yes| COMMIT["Commit vN+1<br/>changelog cites claim IDs"]
    COMMIT --> STOP{"Max cycles or<br/>no gain?"}
    DROP --> STOP
    STOP -->|no| START
    STOP -->|yes| TEST["Score v0 and vFinal<br/>on held-out TEST split"]
    TEST --> PROOF(["Before / after report"])
```

Overreach shows up as a rejection. For example, "always deny delivery-app claims" scores worse on validation because of the drivers who have the endorsement, so the loop drops it.

## 4. Evaluation design

```mermaid
flowchart LR
    subgraph Splits
        TR["Train ~80<br/>'last quarter'<br/>learn from"]
        VA["Validation ~40<br/>accept / reject edits"]
        TE["Test ~60<br/>'this month'<br/>never seen by loop"]
    end
    TR --> LOOP["Improvement loop"]
    VA --> LOOP
    LOOP --> FINAL["Playbook vFinal"]
    TE --> PROOF["Before / after:<br/>v0 vs vFinal"]
    FINAL --> PROOF
```

**Cost-weighted penalty per claim** (weights from plan.md):

| Mistake | Weight |
|---|---|
| Wrongful denial | 10 |
| Missed fraud / wrongful payment | 5 |
| Injury sent to fast-track | 3 |
| Missed subrogation | 2 |
| Wrong fault %, wrong deductible, wrong route | 1 |
| Simple claim over-escalated | 1 |

Each claim's score = 1 − penalty / max penalty. This fills the `evaluation` column, for example 60% or 70%. The version-level report has:

- cost-weighted score
- exact-match rate
- wrongful denials (must stay 0)
- straight-through rate
- SIU hit rate
- subrogation caught
- Crusoe tokens and cost

## 5. Component responsibilities

| Component | Responsibility | Tech |
|---|---|---|
| Inference client | Worker and optimizer models | `langchain-openai` `ChatOpenAI` → Crusoe base URL |
| Triage team | 5 role agents, structured output | LangGraph `StateGraph`, Pydantic |
| Playbook | Plain-English rules, versioned, rollback | YAML + git |
| Guardrails | Sign-off on denials, injury to human, rule linter | Plain Python (deterministic) |
| Dataset | `dataset.csv` rows → claims; stratified train / val / test | Python (`triage/dataset.py`) |
| Gold extractor | Adjuster's free-text decision → structured gold, cached in `data/gold_structured.jsonl` for review | Optimizer model, one time (`triage/gold.py`) |
| Scorer | Cost-weighted score, reports | Plain Python |
| Supervisor | Reflect on failures, propose edits | Optimizer model |
| Loop | Propose → validate → commit or reject | Python |

## 6. Where Crusoe fits

- Serves **every model call**: the 5 agents per claim, the supervisor, and the one-time gold extraction.
- The loop is **inference-heavy**: claims × agents × candidate edits × seeds, in every cycle. Cheap, fast open-model inference is what makes the loop affordable.
- Two model tiers (a small worker and a strong optimizer) give a **score-per-dollar** story.

## 7. Open decisions

1. **Policyholder side:** always the rear driver, or either car (adds subrogation)?
2. **States:** CA + TX only (both at-fault), or add a no-fault state?
3. **Claim inputs:** FNOL text + policy only, or also a police report and repair estimate?
4. **What the loop may change:** playbook rules only, or also agent prompts, tools and team topology?
5. **Gold labels:** ~~oracle~~ decided: `dataset.csv` (extended to ~2,000 rows) is the answer key. Still open: add structured gold columns to the CSV directly, or keep the LLM extraction + human review of `gold_structured.jsonl`?
6. **Models on Crusoe:** which worker and optimizer models? (Check availability and structured-output support.)
7. **Tracing:** local JSONL, or LangSmith / Langfuse?
8. **Acceptance gate:** score threshold, number of seeds, per-category tolerance.
9. **Demo surface:** CLI report, HTML scorecard, or a UI showing the playbook diffs.
