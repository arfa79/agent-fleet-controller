from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EventKind(str, Enum):
    AGENT_STARTED = "agent_started"
    MODEL_WAIT_STARTED = "model_wait_started"
    MODEL_WAIT_FINISHED = "model_wait_finished"
    TOOL_WAIT_STARTED = "tool_wait_started"
    TOOL_WAIT_FINISHED = "tool_wait_finished"
    HUMAN_WAIT_STARTED = "human_wait_started"
    HUMAN_WAIT_FINISHED = "human_wait_finished"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"


WAIT_PAIRS: dict[EventKind, EventKind] = {
    EventKind.MODEL_WAIT_STARTED: EventKind.MODEL_WAIT_FINISHED,
    EventKind.TOOL_WAIT_STARTED: EventKind.TOOL_WAIT_FINISHED,
    EventKind.HUMAN_WAIT_STARTED: EventKind.HUMAN_WAIT_FINISHED,
}

WAIT_FINISHES = {finish: start for start, finish in WAIT_PAIRS.items()}
TERMINAL_EVENTS = {EventKind.AGENT_COMPLETED, EventKind.AGENT_FAILED}


@dataclass(frozen=True, slots=True)
class Resources:
    cpu_cores: float
    memory_gib: float


@dataclass(frozen=True, slots=True)
class TraceEvent:
    version: int
    timestamp: float
    agent_id: str
    kind: EventKind
    resources: Resources | None = None
    workload_class: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTrace:
    agent_id: str
    resources: Resources
    workload_class: str | None
    events: tuple[TraceEvent, ...]
