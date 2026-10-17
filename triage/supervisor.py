"""Claims Supervisor: reads the team's mistakes and past edits, proposes the next config changes."""

import json
from typing import Callable, Literal

from pydantic import BaseModel, Field

from triage.dataset import Claim
from triage.llm import invoke_json
from triage.playbook import ROLES, Playbook, Role, Rule
from triage.scorer import ClaimScore, Report

MAX_FAILURES_SHOWN = 15


class Edit(BaseModel):
    op: Literal["add", "edit", "retire", "instruct"]
    rule_id: str | None = None  # required for edit / retire
    text: str = ""
    role: Role = "decider"
    state: Literal["CA", "TX", "all"] = "all"
    rationale: str = ""
    source_claim_ids: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        if self.op == "instruct":
            return f"instruct [{self.role}] {self.text}"
        target = f" {self.rule_id}" if self.rule_id else ""
        return f"{self.op}{target} [{self.role}/{self.state}] {self.text}".strip()


ProposeFn = Callable[[list[dict], Playbook, list[dict]], list[Edit]]

SYSTEM = """You are the claims supervisor for an auto claims triage team made of these agents: {roles}.
Below are the claims the team got wrong in its latest run compared with the senior adjuster's final decision,
the current team config (house rules and per-role instructions), and the history of edits you tried before.
Work out the root causes, then propose up to {k} edits for the next run.

Edit types:
- "add": a new house rule for one role (optionally scoped to state CA or TX).
- "edit": rewrite an existing rule (give rule_id).
- "retire": remove an existing rule that caused mistakes (give rule_id).
- "instruct": replace the extra instructions for one role (text = the full new instructions for that role).

Rules for good edits:
- Plain English a claims supervisor can approve: one or two sentences per rule.
- General patterns only. Never mention claim numbers, policy numbers, dates, carrier names or copy claim wording.
- Give each rule to the agent role that makes that call; scope to CA or TX only when the law differs by state.
- Fix the most expensive mistakes first: wrongful denial > missed fraud / wrongful payment > injury fast-tracked
  > missed subrogation > the rest.
- Learn from history: do not re-propose edits that were rolled back; build on edits that were kept.
- Never use protected attributes or proxies for them (ZIP code, neighbourhood, race, gender, age, religion, credit score).

Reply with JSON only:
{{"edits": [{{"op": "add" | "edit" | "retire" | "instruct", "rule_id": "R<n>" or null, "text": str,
  "role": one of {roles}, "state": "CA" | "TX" | "all", "rationale": str, "source_claim_ids": [str]}}]}}"""


def failures(claims: list[Claim], report: Report) -> list[dict]:
    by_id = {c.id: c for c in claims}
    worst: list[ClaimScore] = sorted((s for s in report.scores if s.mistakes), key=lambda s: -s.penalty)
    return [
        {
            "claim_id": s.claim_id,
            "state": by_id[s.claim_id].state,
            "description": by_id[s.claim_id].description,
            "adjuster_decision": by_id[s.claim_id].gold_text,
            "team_decision": report.preds[s.claim_id].brief(),
            "team_rationale": report.preds[s.claim_id].rationale,
            "mistakes": s.mistakes,
        }
        for s in worst
    ]


def make_propose_fn(llm, k: int = 3) -> ProposeFn:
    def propose(fails: list[dict], playbook: Playbook, history: list[dict]) -> list[Edit]:
        system = SYSTEM.format(roles=list(ROLES), k=k)
        config = {
            "rules": [r.model_dump(include={"id", "text", "role", "state"}) for r in playbook.active()],
            "role_instructions": playbook.role_instructions,
        }
        user = (f"Current config (v{playbook.version}):\n{json.dumps(config, indent=1)}\n\n"
                f"Edit history (oldest first):\n{json.dumps(history[-10:], indent=1)}\n\n"
                f"Mistakes in the latest run ({len(fails)} claims, worst first):\n"
                f"{json.dumps(fails[:MAX_FAILURES_SHOWN], indent=1)}")
        out = invoke_json(llm, system, user, tag="supervisor")
        items = out.get("edits", []) if isinstance(out, dict) else out
        edits = []
        for item in items[:k]:
            try:
                edits.append(Edit.model_validate(item))
            except ValueError:
                continue
        return edits

    return propose


def apply_edit(playbook: Playbook, edit: Edit, new_version: int) -> Playbook:
    pb = playbook.model_copy(deep=True)
    pb.version = new_version
    if edit.op == "instruct":
        pb.role_instructions[edit.role] = edit.text
        return pb
    if edit.op == "add":
        pb.rules.append(Rule(id=pb.next_rule_id(), text=edit.text, role=edit.role, state=edit.state,
                             source_claim_ids=edit.source_claim_ids, added_in=new_version))
        return pb
    target = next((r for r in pb.rules if r.id == edit.rule_id and r.status == "active"), None)
    if target is None:
        raise ValueError(f"no active rule {edit.rule_id!r} to {edit.op}")
    if edit.op == "retire":
        target.status = "retired"
    else:
        target.text, target.role, target.state = edit.text, edit.role, edit.state
        target.source_claim_ids = sorted(set(target.source_claim_ids) | set(edit.source_claim_ids))
    return pb
