from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable, Protocol
from uuid import uuid4

from .schema import Step, Trajectory


@dataclass(frozen=True)
class Observation:
    text: str
    facts: dict = field(default_factory=dict)
    success: bool = False
    terminated: bool = False
    truncated: bool = False
    score: float = 0.0
    reason: str | None = None
    raw_text: str | None = None


@dataclass(frozen=True)
class Proposal:
    action: str
    thoughts: tuple[str, ...] = ()
    inference_tokens: int = 0


class Environment(Protocol):
    # Return a complete state token only when the backend can certify restoration.
    restoration: str

    def reset(self, seed: int) -> Observation: ...
    def step(self, action: str) -> Observation: ...
    def state_token(self) -> str | None: ...
    def close(self) -> None: ...


class Actor(Protocol):
    identity: str

    def act(self, task: str, initial: str, steps: tuple[Step, ...],
            observation: Observation, seed: int) -> Proposal: ...


def visible_facts(benchmark: str, observation: Observation) -> dict:
    return deepcopy({**observation.facts, "benchmark": benchmark, "observation": observation.text})


def observation_result(observation: Observation) -> dict:
    return {"success": observation.success, "terminated": observation.terminated,
            "truncated": observation.truncated, "score": observation.score,
            "reason": observation.reason, "raw_observation": observation.raw_text}


def is_terminal(observation: Observation) -> bool:
    return observation.success or observation.terminated or observation.truncated


def collect_attempt(environment: Environment | Callable[[], Environment], actor: Actor, *, benchmark: str,
                    task_id: str, task: str, run_id: str, environment_config: dict,
                    max_steps: int = 50, seed: int = 0, attempt: int = 0,
                    constraints: tuple[str, ...] = (), token_budget: int | None = None,
                    on_step: Callable[[Step], None] | None = None) -> Trajectory:
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("max_steps must be positive")
    if token_budget is not None and (type(token_budget) is not int or token_budget < 1):
        raise ValueError("token_budget must be positive or null")
    created_at = datetime.now(timezone.utc).isoformat()
    started = perf_counter()
    steps, tokens, calls, actor_calls = [], 0, 0, 0
    initial, initial_facts, initial_result, initial_token = "", None, None, None
    observation, error, pending = None, None, None
    outcome, reason, phase = "error", "collection_error", "environment_setup"
    env = None
    metadata = deepcopy(environment_config)
    try:
        env = environment() if callable(environment) else environment
        metadata = deepcopy(getattr(env, "metadata", environment_config))
        phase = "reset"
        observation = env.reset(seed)
        initial = observation.text
        initial_facts = visible_facts(benchmark, observation)
        initial_result = observation_result(observation)
        phase = "state_token"
        initial_token = env.state_token()
        for index in range(max_steps):
            if is_terminal(observation):
                break
            if token_budget is not None and tokens >= token_budget:
                reason = "inference_budget_exhausted"
                break
            phase = "actor"
            actor_calls += 1
            proposal = actor.act(task, initial, tuple(steps), observation, seed + index)
            if type(proposal.inference_tokens) is not int or proposal.inference_tokens < 0:
                raise ValueError("Actor must report a nonnegative integer inference token count")
            tokens += proposal.inference_tokens
            pending = {"action": proposal.action, "thoughts": proposal.thoughts,
                       "inference_tokens": proposal.inference_tokens, "actor_seed": seed + index}
            if token_budget is not None and tokens > token_budget:
                reason = "inference_budget_exceeded"
                break
            facts = visible_facts(benchmark, observation)
            phase = "environment_step"
            calls += 1
            observation = env.step(proposal.action)
            steps.append(Step(proposal.action, observation.text, proposal.thoughts, facts,
                              proposal.inference_tokens, seed + index,
                              visible_facts(benchmark, observation), observation_result(observation)))
            pending = None
            phase = "state_token"
            steps[-1] = replace(steps[-1], state_token_after=env.state_token())
            if on_step is not None:
                phase = "progress_output"
                on_step(steps[-1])
        outcome = ("success" if observation.success else "truncated" if observation.truncated
                   else "failure" if observation.terminated else "truncated")
        if reason == "collection_error":
            reason = observation.reason or ("environment_success" if observation.success else
                     "environment_truncated" if observation.truncated else
                     "environment_terminated" if observation.terminated else "step_budget_exhausted")
    except (Exception, KeyboardInterrupt) as exc:
        reported_tokens = getattr(exc, "inference_tokens", None)
        usage_known = type(reported_tokens) is int and reported_tokens >= 0
        if phase == "actor" and usage_known:
            tokens += reported_tokens
        error = {"phase": phase, "type": type(exc).__name__,
                 "message": str(exc) or "Interrupted by user",
                 "pending_proposal": pending,
                 "raw_actor_response": getattr(exc, "raw_response", None),
                 "inference_usage_complete": phase != "actor" or usage_known,
                 "environment_state_uncertain": phase == "environment_step"}
        outcome, reason = "error", "collection_error"
    finally:
        try:
            if env is not None:
                env.close()
        except Exception as exc:
            cleanup = {"type": type(exc).__name__, "message": str(exc)}
            if error is None:
                error = {"phase": "close", **cleanup}
                outcome, reason = "error", "cleanup_error"
            else:
                error["cleanup_error"] = cleanup
    metrics = {"environment_steps": len(steps), "environment_step_calls": calls,
               "actor_calls": actor_calls, "inference_tokens": tokens,
               "inference_usage_complete": not error or error.get("inference_usage_complete", True),
               "elapsed_seconds": perf_counter() - started, "pending_proposal": pending}
    return Trajectory(id=uuid4().hex, benchmark=benchmark, task_id=task_id, task=task,
                      run_id=run_id, outcome=outcome, steps=tuple(steps),
                      environment=metadata, actor=actor.identity,
                      constraints=constraints, initial_observation=initial, seed=seed, attempt=attempt,
                      actor_config=deepcopy(getattr(actor, "config", {"identity": actor.identity})),
                      budget={"max_environment_steps": max_steps, "max_inference_tokens": token_budget},
                      initial_facts=initial_facts, initial_result=initial_result,
                      initial_state_token=initial_token, metrics=metrics,
                      terminal=observation_result(observation) if observation is not None else {},
                      termination_reason=reason, error=error, created_at=created_at)
