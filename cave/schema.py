from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
from typing import Any, Literal


def digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False).encode("utf-8")).hexdigest()


def text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True)
class Predicate:
    path: str
    op: Literal["eq", "ne", "contains", "exists"]
    value: Any = None

    def __post_init__(self):
        text(self.path, "predicate.path")
        if self.op not in {"eq", "ne", "contains", "exists"}:
            raise ValueError(f"Unsupported predicate operation: {self.op}")
        if self.op == "exists" and not isinstance(self.value, bool):
            raise ValueError("exists predicates require a boolean value")

    def evaluate(self, facts: dict) -> bool | None:
        current: Any = facts
        for component in self.path.split("."):
            if not isinstance(current, dict) or component not in current:
                return (self.value is False) if self.op == "exists" else None
            current = current[component]
        if self.op == "exists":
            return self.value
        if self.op == "eq":
            return current == self.value
        if self.op == "ne":
            return current != self.value
        try:
            return self.value in current
        except TypeError:
            return None


@dataclass(frozen=True)
class Step:
    action: str
    observation: str
    thoughts: tuple[str, ...] = ()
    facts_before: dict = field(default_factory=dict)
    inference_tokens: int | None = None
    actor_seed: int | None = None
    facts_after: dict | None = None
    result: dict | None = None
    state_token_after: str | None = None

    def __post_init__(self):
        text(self.action, "step.action")
        if not isinstance(self.observation, str) or not isinstance(self.facts_before, dict):
            raise ValueError("A step requires an observation string and facts object")
        if self.inference_tokens is not None and (
                type(self.inference_tokens) is not int or self.inference_tokens < 0):
            raise ValueError("step.inference_tokens must be a nonnegative integer or null")


@dataclass(frozen=True)
class Trajectory:
    id: str
    benchmark: Literal["alfworld", "webshop"]
    task_id: str
    task: str
    run_id: str
    outcome: Literal["success", "failure", "truncated", "error", "unknown"]
    steps: tuple[Step, ...]
    environment: dict
    actor: str = "unknown"
    constraints: tuple[str, ...] = ()
    reflections: tuple[str, ...] = ()
    initial_observation: str = ""
    raw_text: str = ""
    seed: int = 0
    attempt: int = 0
    actor_config: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    initial_facts: dict | None = None
    initial_result: dict | None = None
    initial_state_token: str | None = None
    metrics: dict = field(default_factory=dict)
    terminal: dict = field(default_factory=dict)
    termination_reason: str = "unspecified"
    error: dict | None = None
    created_at: str | None = None

    def __post_init__(self):
        for name in ("id", "task_id", "task", "run_id", "actor"):
            text(getattr(self, name), name)
        if self.benchmark not in {"alfworld", "webshop"}:
            raise ValueError("Only alfworld and webshop are supported")
        if self.outcome not in {"success", "failure", "truncated", "error", "unknown"}:
            raise ValueError("Invalid trajectory outcome")
        if not isinstance(self.environment, dict):
            raise ValueError("Trajectory requires environment metadata")
        if not self.steps and self.outcome == "unknown":
            raise ValueError("An unknown trajectory requires at least one step")
        if type(self.seed) is not int:
            raise ValueError("Trajectory seed must be an integer")

    @classmethod
    def from_dict(cls, data: dict) -> Trajectory:
        data = dict(data)
        data["steps"] = tuple(Step(**{**s, "thoughts": tuple(s.get("thoughts", ()))})
                              for s in data["steps"])
        for name in ("constraints", "reflections"):
            data[name] = tuple(data.get(name, ()))
        return cls(**data)

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class Candidate:
    source_id: str
    step_index: int
    mechanism: str
    correction: str
    P: tuple[Predicate, ...]
    N: tuple[Predicate, ...]
    failure_signature: str
    validity_limits: tuple[str, ...]
    rationale: str
    contrast_id: str | None = None

    def __post_init__(self):
        for name in ("source_id", "mechanism", "correction", "failure_signature", "rationale"):
            text(getattr(self, name), name)
        if type(self.step_index) is not int or self.step_index < 0:
            raise ValueError("step_index must be a nonnegative integer")
        if not self.P or not self.validity_limits:
            raise ValueError("Candidates require applicability predicates and validity limits")
        for limit in self.validity_limits:
            text(limit, "validity_limit")

    @classmethod
    def from_dict(cls, data: dict) -> Candidate:
        data = dict(data)
        for name in ("P", "N"):
            data[name] = tuple(Predicate(**p) for p in data.get(name, ()))
        data["validity_limits"] = tuple(data["validity_limits"])
        return cls(**data)

    def validate_source(self, trajectories: dict[str, Trajectory]) -> Trajectory:
        if self.source_id not in trajectories:
            raise ValueError(f"Unknown source trajectory: {self.source_id}")
        source = trajectories[self.source_id]
        if source.outcome not in {"failure", "truncated"}:
            raise ValueError("Memory source must be a failed or truncated trajectory")
        if self.step_index >= len(source.steps):
            raise ValueError("Candidate step is outside the source trajectory")
        step = source.steps[self.step_index]
        if self.correction.strip() == step.action.strip():
            raise ValueError("Correction must differ from the original action")
        if self.failure_signature not in step.observation:
            raise ValueError("Failure signature must occur in the cited source observation")
        if self.contrast_id is not None:
            contrast = trajectories.get(self.contrast_id)
            if contrast is None or contrast.outcome != "success":
                raise ValueError("Contrast must reference a successful trajectory")
            if not compatible(source, contrast):
                raise ValueError("Contrast must share benchmark, task, constraints and environment")
        return source


def compatible(left: Trajectory, right: Trajectory) -> bool:
    return (left.benchmark, left.task_id, left.task, left.constraints, left.environment) == (
        right.benchmark, right.task_id, right.task, right.constraints, right.environment)


@dataclass(frozen=True)
class Memory:
    id: str
    p_minus: dict
    a_minus: str
    f: str
    a_tilde: str
    P: tuple[Predicate, ...]
    N: tuple[Predicate, ...]
    E: dict
    q: dict

    @property
    def eligible(self) -> bool:
        return self.q.get("status") == "verified"

    def to_dict(self) -> dict:
        return asdict(self)


def finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)
