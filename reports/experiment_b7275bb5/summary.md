# Improvement experiment: stopped after cycle 2

**The learning loop worked and selected a playbook with better validation results. Overall improvement has not been proven: the latest training rerun declined slightly, and the hidden test was not run.**

The live experiment was stopped at the user's request after the current cycle completed. No further inference was performed to produce this report.

## Data and setup

- All original 50 dataset.csv rows were used as training. None were assigned to validation or hidden test.
- Created and froze 24 separate synthetic validation claims and 24 separate synthetic hidden test claims before learning.
- IDs and exact normalized descriptions do not overlap across the three datasets; dataset hashes remain unchanged.
- Worker: openai/gpt-oss-120b. Supervisor: deepseek-ai/DeepSeek-V4-Pro. Both served through Crusoe.
- Three cycles were planned. Two cycles completed, with three independent candidate edits in each. One edit was saved.

## Results

| Metric | v0 | Selected v1 | Change |
|---|---:|---:|---:|
| Validation cost-weighted score (24 new claims) | 79.4% | 87.7% | +8.3 percentage points |
| Validation exact decision match | 25.0% | 37.5% | +12.5 percentage points |
| Validation wrongful denials | 6 | 3 | 3 fewer |
| Validation model execution errors | 0 | 0 | No unrecovered errors |
| Training cost-weighted score (all 50 original claims) | 81.9% | 81.4% | -0.5 percentage points |
| Hidden test | Not evaluated | Not evaluated | Stopped before final comparison |

Training v1 is the completed rerun that supplied cycle 2 mistakes. Its 81.4% score is retained from console output; intermediate per-claim predictions were not saved. The score is not a new inference or a reconstructed result. The cost-weighted score is a normalized penalty score, not percentage accuracy.

## How the loop ran

1. Evaluated v0 on all 50 training claims and the separate 24-claim validation set.
2. The supervisor reviewed 33 training claims with scored mistakes and proposed three edits.
3. Each edit was independently applied to v0 and measured on the same frozen validation set.
4. Required at least a one-percentage-point validation gain, no increase in wrongful denials, and zero candidate execution errors.
5. Saved the highest-scoring qualifying edit as v1; rules, source training claim IDs, and validation gain are in playbook/v1.yaml.
6. Reran all 50 training claims with v1. The score was 81.4%, with 37 claims having scored mistakes. The supervisor proposed three more edits.
7. All cycle 2 candidates failed the gate. Retained v1 and stopped at the user's request.

## Learned rule

> If the claim involves any bodily injury, airbag deployment, a repair estimate over $10,000, or disputed liability, do not fast-track. Route to an adjuster with human sign-off.

This instruction is appended to the Decider prompt through the playbook. Model weights and team structure were unchanged.

## Candidate history

| Cycle | Candidate | Validation score | Outcome |
|---|---|---:|---|
| 1 | Unendorsed commercial use goes to coverage review | 87.3% | Passed, but another candidate scored higher |
| 1 | Late notice and unresolved policy validity go to review | 85.6% | Passed, but another candidate scored higher |
| 1 | Injury, severe damage, or disputed liability goes to an adjuster | 87.7% | Selected as v1 |
| 2 | Broader prohibition on denials for business use, lateness, or cancellation | 86.2% | Rejected: score decreased |
| 2 | Broader rear-end payment/subrogation instruction | 84.4% | Rejected: wrongful denials increased from 3 to 4 |
| 2 | Additional disputed-liability routing instruction | 83.8% | Rejected: wrongful denials increased from 3 to 4 |

## Changes made to support this run

- Added the experiment command to orchestrate training, separate validation, improvement, and final comparisons, with unique report folders.
- Added separate labeled benchmark loading and overlap checks.
- Fixed audit logging so only the actual selected edit is marked accepted; preserved all proposal details and validation summaries.
- Added rate-limit backoff and baseline resumption. A first attempt hit a rate limit; the resumed run reused 73 successful baseline decisions and retried the failed validation claim.
- Retained the earlier scoring, error-credit, gold-consistency, and injury/human-signoff fixes.
- All 26 offline tests passed before the resumed live run.

## Missing logic and limits

- The hidden test comparison was not executed because the user requested stopping after the current cycle.
- Validation and hidden test fixtures are synthetic and share existing demo scenario families; gold is not insurance-expert reviewed.
- Each candidate was measured once; two existing v0 runs on 26 overlapping claims differed by 1.54 percentage points and 8 decision records.
- The acceptance threshold is 1 percentage point and can therefore admit model variability; repeated paired validation is needed.
- The gate prevents increases in wrongful denials but does not require zero; selected v1 still has three validation wrongful denials.
- Intermediate per-claim training predictions and selected-candidate validation predictions were not checkpointed before the stop; their saved summaries and console score are reported without reconstructing missing decisions.
- The runner currently lacks a graceful stop-at-cycle flag and a request/time budget. It was stopped using the saved cycle checkpoint.
- The supervisor sees current training failures and the current playbook; it does not yet receive a persistent history of rejected edits or full intermediate agent traces.

## Files

- [Complete experiment record](experiment.json)
- [All six proposals, scores, reasons, and selected edit](improvement_log.json)
- [All 50 original training baseline decisions](v0_train.json)
- [Separate validation baseline decisions](v0_validation.json)
- [Observed repeatability check](repeatability_check.json)
- [Stop checkpoint](stop_checkpoint.json)
- [Saved learned playbook](../../playbook/v1.yaml)
- [Original 50-claim baseline CSV](../../runs/experiment_b7275bb5/dataset_scored_v0_train.csv)
- [Separate validation baseline CSV](../../runs/experiment_b7275bb5/dataset_scored_v0_validation.csv)

No final v1 per-claim CSV or hidden-test report was produced before the stop.
