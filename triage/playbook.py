"""Versioned, human-readable playbook of learned rules (playbook/vN.yaml)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from triage.config import PLAYBOOK_DIR

Role = Literal["intake", "coverage", "liability", "fraud", "decider"]
ROLES: tuple[Role, ...] = ("intake", "coverage", "liability", "fraud", "decider")


class Rule(BaseModel):
    id: str
    text: str
    role: Role
    state: Literal["CA", "TX", "all"] = "all"
    source_claim_ids: list[str] = Field(default_factory=list)
    added_in: int = 0
    status: Literal["active", "retired"] = "active"
    score_delta: float | None = None


class Playbook(BaseModel):
    """One versioned team config: learned rules plus per-role prompt add-ons. Base prompts never change."""

    version: int = 0
    rules: list[Rule] = Field(default_factory=list)
    role_instructions: dict[Role, str] = Field(default_factory=dict)
    changelog: list[str] = Field(default_factory=list)

    def active(self) -> list[Rule]:
        return [r for r in self.rules if r.status == "active"]

    def rules_for(self, role: Role, state: str) -> list[Rule]:
        return [r for r in self.active() if r.role == role and r.state in ("all", state)]

    def render(self, role: Role, state: str) -> str:
        out = ""
        if self.role_instructions.get(role):
            out += f"\n\nAdditional instructions from your supervisor:\n{self.role_instructions[role]}"
        rules = self.rules_for(role, state)
        if rules:
            lines = "\n".join(f"- [{r.id}] {r.text}" for r in rules)
            out += f"\n\nHouse rules from the claims manual (apply them and cite the rule id in rules_cited):\n{lines}"
        return out

    def next_rule_id(self) -> str:
        nums = [int(r.id[1:]) for r in self.rules if r.id[1:].isdigit()]
        return f"R{max(nums, default=0) + 1}"


def path_for(version: int, directory: Path = PLAYBOOK_DIR) -> Path:
    return directory / f"v{version}.yaml"


def load(version: int, directory: Path = PLAYBOOK_DIR) -> Playbook:
    return Playbook.model_validate(yaml.safe_load(path_for(version, directory).read_text()))


def dump(pb: Playbook) -> str:
    return yaml.safe_dump(pb.model_dump(), sort_keys=False, allow_unicode=True, width=120)


def save(pb: Playbook, directory: Path = PLAYBOOK_DIR) -> Path:
    p = path_for(pb.version, directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump(pb))
    return p


def latest_version(directory: Path = PLAYBOOK_DIR) -> int:
    return max((int(p.stem[1:]) for p in directory.glob("v*.yaml") if p.stem[1:].isdigit()), default=0)
