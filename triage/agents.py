"""The triage team as a LangGraph: Intake -> Coverage -> (Liability -> Fraud) -> Decider -> Guardrails.

Each agent's system prompt = day-1 base prompt + the playbook rules for its role and the claim's state.
"""

import json
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from triage import guardrails
from triage.dataset import Claim
from triage.llm import invoke_json
from triage.playbook import Playbook, Role
from triage.schemas import TriageDecision

BASE_PROMPTS: dict[Role, str] = {
    "intake": """You are the intake clerk on a US auto insurer's claims team.
Read the first notice of loss (FNOL) and extract the facts your colleagues need. Do not decide coverage or fault.
Reply with JSON only:
{"summary": str, "insured_vehicle_position": "rear" | "front" | "unknown", "injury": bool, "facts": [str]}""",
    "coverage": """You are the coverage analyst on a US auto insurer's claims team.
Decide whether our policy responds to this loss: check who was driving, policy status and dates, coverage types and use of the vehicle.
Reply with JSON only:
{"coverage": "covered" | "denied" | "coverage_review", "covers_other_party": bool, "covers_own_vehicle": bool,
 "reason": str, "rules_cited": [str]}""",
    "liability": """You are the liability adjuster on a US auto insurer's claims team.
Decide what percentage of fault belongs to our insured, and whether we can recover from another carrier.
Reply with JSON only:
{"fault_pct": int, "subrogation": bool, "reason": str, "rules_cited": [str]}""",
    "fraud": """You are the fraud screener on a US auto insurer's claims team.
Decide whether this claim should be referred to the special investigations unit (SIU).
Reply with JSON only:
{"siu_referral": bool, "reason": str, "rules_cited": [str]}""",
    "decider": """You are the triage decider on a US auto insurer's claims team.
Combine your colleagues' notes into the final triage decision.
Routes: fast_track (simple, pay now), adjuster (needs a human adjuster), siu (fraud review),
subrogation (pay now, recover from the other carrier), coverage_review (coverage question), deny.
Reply with JSON only:
{"coverage": "covered" | "denied" | "coverage_review", "fault_pct": int or null, "pay_other_party": bool,
 "pay_own_vehicle": bool, "deductible_applies": bool,
 "route": "fast_track" | "adjuster" | "siu" | "subrogation" | "coverage_review" | "deny",
 "siu_referral": bool, "subrogation": bool, "human_signoff": bool, "rationale": str, "rules_cited": [str]}""",
}


class TeamState(TypedDict, total=False):
    claim: dict
    notes: dict
    decision: TriageDecision


def _user_message(state: TeamState) -> str:
    c = state["claim"]
    notes = json.dumps(state.get("notes", {}), indent=1)
    return f"Claim {c['id']} | State: {c['state']}\nFNOL: {c['description']}\n\nNotes from colleagues so far:\n{notes}"


def build_team(llm, playbook: Playbook):
    def agent(role: Role):
        def run(state: TeamState) -> TeamState:
            system = BASE_PROMPTS[role] + playbook.render(role, state["claim"]["state"])
            out = invoke_json(llm, system, _user_message(state))
            return {"notes": {**state.get("notes", {}), role: out}}

        return run

    def decide(state: TeamState) -> TeamState:
        system = BASE_PROMPTS["decider"] + playbook.render("decider", state["claim"]["state"])
        decision = TriageDecision.model_validate(invoke_json(llm, system, _user_message(state)))
        notes = state.get("notes", {})
        cited = {r for n in notes.values() if isinstance(n, dict) for r in n.get("rules_cited", []) or []}
        decision.rules_cited = sorted(cited | set(decision.rules_cited))
        injury = bool(notes.get("intake", {}).get("injury"))
        return {"decision": guardrails.enforce(decision, injury)}

    def after_coverage(state: TeamState) -> str:
        return "liability" if state["notes"].get("coverage", {}).get("coverage") == "covered" else "decider"

    g = StateGraph(TeamState)
    for role in ("intake", "coverage", "liability", "fraud"):
        g.add_node(role, agent(role))
    g.add_node("decider", decide)
    g.add_edge(START, "intake")
    g.add_edge("intake", "coverage")
    g.add_conditional_edges("coverage", after_coverage, ["liability", "decider"])
    g.add_edge("liability", "fraud")
    g.add_edge("fraud", "decider")
    g.add_edge("decider", END)
    return g.compile()


TriageFn = Callable[[Claim, Playbook], TriageDecision]


def make_triage_fn(llm) -> TriageFn:
    compiled: dict[int, object] = {}

    def triage(claim: Claim, playbook: Playbook) -> TriageDecision:
        key = id(playbook)
        if key not in compiled:
            compiled[key] = build_team(llm, playbook)
        result = compiled[key].invoke({"claim": {"id": claim.id, "state": claim.state, "description": claim.description}})
        return result["decision"]

    return triage
