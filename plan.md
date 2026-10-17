# Business case: a motor claims triage team that learns from its own mistakes

## The problem

Auto is the highest-volume claims line a carrier has. Every claim starts with a first notice of loss (FNOL): the customer reports what happened. Someone then has to make four calls quickly:

- **Coverage:** which parts of the policy pay, if any?
- **Fault:** how much of it is our policyholder's?
- **Money:** which deductible applies, and roughly how big a reserve to set aside?
- **Route:** fast-track it, send it to an adjuster, flag it for fraud review (the special investigations unit, SIU), or recover the money from the other insurer?

Mistakes go both ways, and they cost different amounts. Paying a claim the carrier shouldn't is leakage. Wrongly denying a claim is worse: it brings bad-faith exposure, regulator complaints and lost customers. Missing fraud is leakage that keeps repeating. Sending everything to an adjuster "to be safe" wastes the adjuster time automation was meant to save.

## Why a rear-end collision is the right example

A rear-end crash looks like the easiest claim there is. In most states the car behind is presumed at fault, and any AI agent will tell you "rear driver pays." That's the trap. Change one fact and the right answer flips. Those twists live in the carrier's claims manual and in its senior adjusters' heads, not in a general model's knowledge.

### Same crash, seven different right answers

| # | Same crash, one fact changes | Right triage | What a day-1 agent does |
|---|---|---|---|
| 1 | **Baseline:** our insured rear-ends a car at a red light, about 15 mph, no injuries, police report filed. Policy active, collision coverage with a $500 deductible | Covered. The other car is paid under property-damage liability; our insured's car under collision, minus $500. 100% at fault. Fast-track | ✅ Gets it right. This is the trap |
| 2 | The driver was the insured's nephew, listed as an excluded driver | Deny both. Send the excluded-driver letter | ❌ Pays, because "the car is insured" |
| 3 | The insured was on a food-delivery run with the app on, with no delivery or rideshare endorsement | Coverage question under the business-use exclusion. Send to coverage review, never fast-track | ❌ Pays |
| 4 | The lead car braked hard "for no reason", had 4 occupants, and all reported neck pain days later at the same clinic | Covered, but refer to SIU: this looks like a staged "swoop-and-squat." Injury means an adjuster, not fast-track | ❌ Fast-tracks it |
| 5 | Our insured was the car that got hit | Not at fault. Pay collision now, recover from the other carrier (subrogation), refund the deductible once recovered | ⚠️ Charges the deductible and misses the recovery |
| 6 | The policy was cancelled for non-payment 12 days before the crash, and the grace period is 10 days | Deny: no coverage was in force | ❌ Pays. It never checks dates |
| 7 | Liability-only policy (no collision coverage) | Pay the other car. Our insured's own car isn't covered | ❌ Pays both |

### The rest of the manual follows the same pattern

- A report filed more than 30 days late goes to coverage review.
- An airbag deployment or a big estimate means a field appraiser and a total-loss check.
- In a chain-reaction pile-up, the middle car usually isn't at fault for its front damage.
- If the lead car was reversing or had dead brake lights, the rear-driver presumption weakens.

### State rules: California vs Texas

Both are at-fault (tort) states, so the at-fault driver's liability insurer pays. The differences are in how fault is decided and in the claim-handling rules.

| Topic | California | Texas | Why it matters for triage |
|---|---|---|---|
| Rear-end fault | The following driver is presumed negligent (following too closely). The presumption can be rebutted | No presumption: a rear-end crash alone is not proof of negligence. The claimant must show the rear driver was careless | In TX, don't concede 100% fault automatically, especially if the lead car braked suddenly |
| Comparative fault | Pure comparative: you recover damages minus your own share, even at 99% at fault | Modified comparative (51% bar): you recover nothing if you are more than 50% at fault | Fault splits near 50% change who gets paid in TX |
| Minimum liability limits | 30/60/15 (since Jan 2025) | 30/60/25 | Sets the floor for the other car's property-damage payout |
| PIP / med-pay | Med-pay optional | PIP ($2,500) must be offered and is included unless rejected in writing | In TX, check for a signed PIP rejection before deciding injury coverage for our occupants |
| Excluded drivers | Allowed if the named insured signs the exclusion | Allowed if the named insured signs the exclusion | Confirm the signed exclusion is on file before denying |
| Cancellation for non-payment | Insurer must mail at least 10 days' notice | Insurer must mail at least 10 days' notice | A lapse doesn't count unless notice was mailed; check before denying |
| Subrogation and deductible | Fair Claims regs: include the deductible in the subrogation demand and share the recovery with the insured | No equivalent rule; follow policy terms and the made-whole doctrine | In CA, refunding the deductible is required, not optional |
| Claim deadlines | Acknowledge within 15 days, accept or deny within 40 days | Acknowledge within 15 days, accept or reject within 15 business days of getting all documents (Prompt Payment Act) | TX moves faster, so the time from FNOL to first decision KPI is tighter |
| Suspected fraud | Report to the Dept of Insurance within 60 days | Report to the Dept of Insurance within 30 days | An SIU referral starts a state reporting clock |

> These are general summaries for the demo. Have the legal team check each rule against current statute before using it in production.

## Where the learning comes from

Every carrier has years of closed claims with the senior adjuster's final decision. That's the answer key. The pitch: **your claims history is already a training set; you just never turned it into a playbook.**

- **Day 1:** the agent team has the policy and the claim, but not the house rules. It scores like a smart new hire.
- **Each review cycle:** it compares its calls on last quarter's closed claims with what the adjusters actually decided. It works out why it was wrong and writes or edits a rule in its playbook. A rule is kept only if it measurably improves results.
- **The proof:** score it on this month's claims, which it has never seen, before and after.

## Why a written playbook beats a black box

- **Auditable:** every rule is plain English, links to the claims that taught it, and a claims supervisor can approve or reject it. State regulators following the NAIC's model bulletin on insurers' use of AI expect claim decisions to be explainable and governed. A readable playbook meets that; fine-tuned model weights don't.
- **Reversible:** a bad rule can be rolled back in one click.
- **Fixes its own overreach:** for example, it tries "always deny delivery-app claims," wrongly denies the drivers who have the endorsement, and drops the rule itself.

## What "better" means: a cost-weighted score, not raw accuracy

Plain accuracy can be gamed by escalating everything. So each mistake is priced by its business impact:

| Mistake | Business cost | Weight |
|---|---|---|
| Wrongful denial | Bad faith, complaints, churn | Highest |
| Missed fraud or wrongful payment | Leakage | High |
| Injury claim sent to fast-track | Under-reserving, litigation risk | Medium |
| Simple claim sent to an adjuster | Wasted adjuster time | Low |

## The team mirrors a real claims department

- **Intake clerk:** reads the FNOL story.
- **Coverage analyst:** checks the policy, the drivers and the dates.
- **Liability adjuster:** decides fault.
- **Fraud screener:** looks for staged-accident patterns.
- **Claims supervisor:** reviews mistakes after each cycle and updates the manual.

## Business KPIs to put on the slide

- Share of claims processed with no human touch, with zero wrongful denials
- Triage accuracy on unseen claims (cost-weighted)
- Time from FNOL to first decision
- Leakage caught: excluded drivers, lapsed policies, business use
- Hit rate of SIU referrals
- Subrogation recoveries that would otherwise have been missed

## Guardrails

- The agent recommends; a human signs off every denial.
- Injury claims always reach a human.
- Learned rules can't use protected attributes or proxies for them, such as ZIP code.

## Three questions to settle

1. **Whose side?** Is the policyholder always the rear driver, or either car (which brings in row 5 and subrogation)? Either car is richer, but one side is simpler.
2. **One state or two?** Keep one at-fault state, or add a no-fault state where each driver's own insurer pays for their injuries regardless of fault? That adds a strong "it learned the state rule" moment.
3. **What arrives with the claim?** Just the customer's story plus the policy, or also a police-report summary and a repair estimate? More inputs means more realistic rules, such as total-loss thresholds.
