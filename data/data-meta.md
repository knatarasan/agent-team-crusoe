# dataset.csv: metadata

50 synthetic rear-end auto claims. They are the answer key for scoring the triage team (see [../architecture.md](../architecture.md)).

## Scope

- **Accident type:** rear-end collisions only
- **States:** CA (26 rows), TX (24 rows)
- **Insurers:** GEICO and Progressive only. Every row has one on each side: GEICO is Party A's insurer in 25 rows and Progressive in 25.
- **Accident dates:** 2026-01-08 to 2026-09-04
- **Policy numbers:** made up and unique (`GEI-<state>-<6 digits>`, `PRG-<state>-<6 digits>`)

## Columns

| Column | Meaning |
|---|---|
| `s.no` | Claim ID |
| `state` | CA or TX; decides which state rules apply |
| `accident_date` | Date of loss |
| `party_a_insurer` / `party_a_policy_number` | **Our policyholder.** The carrier triaging the claim is Party A's insurer |
| `party_b_insurer` / `party_b_policy_number` | The other driver; the target of any subrogation |
| `description` | Two-line FNOL story. It contains every fact the gold answer depends on |
| `expected_outcome_gold` | The senior adjuster's final decision (free text) |
| `agent_outcome` | Left empty; filled by `triage eval` in `runs/*.csv` |
| `evaluation` | Left empty; cost-weighted score per claim, e.g. 60% or 70% |

Agents currently read only `state` and `description`.

## Case mix

| Case | Rows | Count | Gold route |
|---|---|---|---|
| Simple at-fault rear-end (incl. a commute "noise" case, row 16) | 1, 6–16, 50 | 13 | fast-track |
| Our insured was hit (incl. pile-ups, rows 22 and 49) | 4, 17–22, 49 | 8 | subrogation |
| Excluded driver, exclusion signed | 2, 23, 24, 26 | 4 | deny + human sign-off |
| Excluded driver, exclusion **not** signed | 25 | 1 | coverage review |
| Delivery / rideshare, **no** endorsement | 27, 28, 29 | 3 | coverage review |
| Delivery **with** endorsement (overreach trap) | 30, 31 | 2 | fast-track |
| Swoop-and-squat (CA 100% fault, TX 50%) | 3, 32, 33, 34 | 4 | SIU (adjuster, never fast-track) |
| Lapsed policy, notice mailed, past grace | 5, 35 | 2 | deny + human sign-off |
| Lapsed policy, within grace period | 36 | 1 | fast-track |
| Lapsed policy, notice **not** mailed | 37 | 1 | coverage review |
| Liability-only (own car not covered) | 38, 39, 40 | 3 | fast-track |
| Reported more than 30 days late | 41, 42 | 2 | coverage review |
| Airbag deployment or large estimate | 43, 44 | 2 | adjuster (field appraiser + total-loss check) |
| Real injury, no red flags | 45, 46 | 2 | adjuster |
| Lead car reversing / dead brake lights (50% fault) | 47, 48 | 2 | adjuster (disputed liability) |
| **Total** | | **50** | |

## What each twist tests

- **Simple rear-ends** are the trap: the day-1 agent gets them right, so the overall average hides learning. Report scores by category.
- **Excluded driver, lapse, delivery and late report** test coverage checks. Paying these is leakage.
- **Unsigned exclusion, lapse within grace, notice not mailed and delivery with endorsement** are the opposite cases. Denying these is a wrongful denial, the most expensive mistake. They catch rules that overreach.
- **Swoop-and-squat** tests fraud screening and the CA vs TX fault difference.
- **Our insured hit and pile-ups** test subrogation and the deductible refund (required in CA).
- **Reversing and dead brake lights** test when the rear-driver presumption weakens, and the TX 51% bar.

## Conventions in the gold text

- **Fault:**
  - CA presumes the following driver negligent, so the default is 100%.
  - TX has no presumption: the default is 100% when the lead car acted lawfully, 50% for sudden braking or a merge with staging signs.
  - Reversing or dead brake lights mean 50% in both states.
- **Human sign-off:** required for every denial and coverage question.
- **Injury claims:** never fast-tracked.
- **Deductible:** refunded from the subrogation recovery when our insured isn't at fault (a regulatory requirement in CA).

## Split (from `triage/dataset.py`)

The split is stratified by gold route, about 50/25/25: roughly 25 train, 12 validation and 13 test claims. Routes with 2–4 rows may have zero or one example in validation or test, so expect noisy scores for those categories until the dataset grows.
