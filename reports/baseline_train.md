# v0 training baseline

Executed against Crusoe Managed Inference using the current dataset and the empty v0 playbook.

| Metric | Result |
|---|---|
| Training claims evaluated | 26 |
| Cost-weighted score | 83.5% |
| Exact decision match | 26.9% |
| Wrongful denials vs supplied gold | 3 |
| Runtime errors | 0 |
| Offline tests | 19 passed |

The cost-weighted score is a normalized penalty score, not percentage accuracy. Exact match compares coverage, fault, payment flags, deductible, route, SIU, subrogation, and human sign-off; rationale and rule citations are excluded.

## Files

- Per-claim decisions, expected text, scores, and mistakes: [scored CSV](../runs/dataset_scored_v0_train.csv).
- Machine-readable summary, structured decisions, and inference token usage: [JSON report](v0_train.json).
- Models, split IDs, dataset hashes, scoring weights, and label notes: [run setup](baseline_setup.json).
- Structured expected answers: [gold cache](../data/gold_structured.jsonl).

## Run configuration

- Worker: `openai/gpt-oss-120b`.
- Gold extractor: `deepseek-ai/DeepSeek-V4-Pro`.
- Split seed: 42. Training: 26; validation: 14; hidden test: 10.
- Validation and hidden test claims were not evaluated by the triage team in this run.
- This run establishes a baseline. No improvement cycle was executed.

Reproduce after environment setup:

```powershell
uv run triage --workers 2 --seed 42 eval --version 0 --split train
```

## Findings

Wrongful denial claim IDs: 46, 29, 28.

| Mistake | Claims |
|---|---|
| wrong_route | 5 |
| missed_payment | 4 |
| deductible_wrong | 4 |
| wrongful_denial | 3 |
| missing_human_signoff | 3 |
| over_escalated | 3 |
| wrongful_payment | 2 |
| missed_fraud | 1 |

## Label limitations

Gold labels were extracted from the supplied expected-outcome text and checked for safety consistency. They have not received an expert review; these results are provisional.

- Claim 3 does not specify an exact fault percentage, and its gold fault is null. This limits interpretation of exact-match results for that claim.
- Claim 5 has a conditional denial dependent on cancellation-notice confirmation, which is not established in the input. Review this label before relying on benchmark results.
- Four extracted labels (43, 44, 47, 48) were normalized to require human sign-off for adjuster routes.

## Code corrections made before evaluation

- Penalize omitted required payments (weight 5) when both decisions agree coverage applies, and missing required human sign-off (weight 3). Full weights are recorded in baseline_setup.json.
- Calculate exact match across decision fields, separately from fault-tolerant penalty scoring.
- Give model execution failures zero score instead of credit for the fallback review decision.
- Require human sign-off for every detected injury, and apply shared safety rules to extracted gold.
