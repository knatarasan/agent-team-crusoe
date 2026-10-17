# Improvement over runs

All 2 runs score the same full dataset.

```mermaid
xychart-beta
    title "Score per run"
    x-axis [1, 2]
    y-axis "score" 0 --> 1
    line [0.906, 0.957]
    line [0.48, 0.58]
```

Lines: average cost-weighted score (first), exact-match rate (second).

| Run | Config | Avg score | Exact match | Wrongful denials | Status | Changes tested |
|---|---|---|---|---|---|---|
| 1 | v0 | 0.906 | 0.48 | 4 | baseline | — |
| 2 | v1 | 0.957 | 0.58 | 0 | kept | add [coverage/all] When a claim involves possible business use of a personal auto policy (delivery, rideshare, or any paid transport), never deny or fast-track. Route to coverage review with human sign-off. ; add [coverage/all] When a policy cancellation or driver exclusion may be invalid due to missing notice or missing signature, do not deny or pay. Route to coverage review with human sign-off. ; add [coverage/all] When a claim is reported more than 30 days after the loss, do not deny or pay. Route to coverage review with human sign-off before any payment decision. |

## Score by category (gold route)

| Category | run 1 | run 2 |
|---|---|---|
| adjuster | 0.942 | 0.95 |
| coverage_review | 0.593 | 0.893 |
| deny | 1.0 | 1.0 |
| fast_track | 0.992 | 0.992 |
| siu | 0.812 | 0.912 |
| subrogation | 0.925 | 0.925 |
