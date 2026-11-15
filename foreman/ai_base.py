"""Shared shapes for the AI agents: the model client interface and the call record.

An "AI agent" here is a role with a real model behind it. Everything else in
Foreman (the planner, the route finder, the safety gate, the recovery monitor)
is rule-based code. The model never assigns jobs and a re-plan never waits on
it: the rule-based result is acted on first, the model's answer is applied
when it arrives, and any failure falls back to the rules.

To switch the model provider (for example to a normal API key), write another
class with the same ask() method. Nothing else changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

AI_TIMEOUT_S = 15.0  # hard limit on every AI call; after it the rules carry on

TOOLS_CALLED = "tools called"  # the model asked for the data through tools
DATA_READ = "data read"        # the same data was passed in the prompt instead


@dataclass
class Tool:
    """A read-only tool. Its answer is frozen before the call, so it cannot change anything."""

    name: str
    description: str
    data: Any                      # JSON-serialisable answer
    select_by: str | None = None   # if set, data is a dict and the model passes this key to pick one entry


@dataclass
class ModelReply:
    ok: bool
    answer: dict | None = None                               # parsed JSON that matched the schema
    tools: list[dict] = field(default_factory=list)          # [{name, args}] in call order
    tools_mode: str = TOOLS_CALLED
    ms: float = 0.0
    error: str | None = None                                 # why it failed; never contains the key


class ModelClient(Protocol):
    name: str  # shown on the dashboard, e.g. "claude-haiku-5-5 via Claude Code"

    def ask(self, system: str, prompt: str, tools: list[Tool], schema: dict,
            timeout_s: float = AI_TIMEOUT_S) -> ModelReply:
        """One call. Must return within timeout_s and must never raise."""
        ...


@dataclass
class AiCall:
    """One AI agent call, as shown in state.foreman.reasoning."""

    agent: str                    # "AI Recovery agent" | "AI Safety agent"
    plan_no: int                  # the plan it was looking at
    at: float                     # simulated minute it was asked
    clock: str
    input: str                    # what it was given, in plain words
    status: str = "thinking"      # thinking | done | fallback
    tools: list[dict] = field(default_factory=list)
    tools_mode: str = TOOLS_CALLED
    answer: dict | None = None
    applied: str = ""             # what Foreman did with the answer
    ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def finish(self, reply: ModelReply, answer: dict | None, error: str | None = None) -> None:
        """Record the outcome. answer is the checked answer, or None to fall back to the rules."""
        self.tools, self.tools_mode, self.ms = reply.tools, reply.tools_mode, round(reply.ms, 1)
        self.answer = answer
        self.error = error or reply.error
        self.status = "done" if answer is not None else "fallback"
