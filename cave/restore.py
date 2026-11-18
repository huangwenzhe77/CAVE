"""Restore before a recorded action without invoking an actor or executing that action."""

from dataclasses import asdict, dataclass, field

from .runtime import Environment, Observation, is_terminal, observation_result, visible_facts
from .schema import Step, Trajectory, digest


@dataclass
class RestoredPoint:
    environment: Environment | None
    observation: Observation | None
    history: tuple[Step, ...]
    report: dict = field(default_factory=dict)

    def close(self):
        if self.environment is not None:
            self.environment.close()
            self.environment = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def restore_before(source: Trajectory, t: int, factory) -> RestoredPoint:
    """t is 1-based: t=1 performs reset only; t=2 replays action 1 only.

    The caller owns the live environment on success and must close it. A CLI
    report is evidence of this run, not a serialized environment checkpoint.
    """
    if type(t) is not int or not 1 <= t <= len(source.steps):
        raise ValueError("t must be a 1-based recorded action number")
    index = t - 1
    step_limit = source.budget.get("max_environment_steps", source.environment.get("max_steps"))
    token_limit = source.budget.get("max_inference_tokens")
    prefix_tokens = [step.inference_tokens for step in source.steps[:index]]
    used_tokens = sum(prefix_tokens) if all(value is not None for value in prefix_tokens) else None
    remaining = {"environment_steps": None if step_limit is None else step_limit - index,
                 "inference_tokens": None if token_limit is None or used_tokens is None
                 else token_limit - used_tokens,
                 "inference_budget_configured": token_limit is not None}
    report = {"source_id": source.id, "source_sha256": source.fingerprint, "t": t,
              "step_index": index, "reset_seed": source.seed, "status": "error", "ready": False,
              "restoration": "unverified", "checks": [], "warnings": [], "errors": [],
              "replayed_steps": 0, "replay_step_calls": 0, "new_actor_calls": 0,
              "new_inference_tokens": 0, "source_prefix_inference_tokens": used_tokens,
              "remaining_budget": remaining, "observation": None, "checkpoint": None,
              "actor": source.actor, "actor_config": source.actor_config,
              "actor_history": {"task": source.task, "constraints": list(source.constraints),
                                "initial_observation": source.initial_observation, "steps": []}}
    point = RestoredPoint(None, None, (), report)

    def check(location, kind, expected, actual):
        matched = expected == actual
        report["checks"].append({"location": location, "kind": kind, "matched": matched,
                                 "expected": expected, "actual": actual})
        if not matched:
            report["status"] = "drift"
            raise ValueError(f"{location}: {kind} drift")

    def compare_observation(location, observation, expected_text, expected_facts, expected_result):
        check(location, "observation", expected_text, observation.text)
        if expected_facts is not None:
            check(location, "visible_facts", expected_facts, visible_facts(source.benchmark, observation))
        else:
            report["warnings"].append(f"{location}: source visible facts unavailable")
        if expected_result is not None:
            check(location, "result", expected_result, observation_result(observation))
        else:
            report["warnings"].append(f"{location}: source terminal metadata unavailable")

    try:
        point.environment = env = factory(source.benchmark, source.environment)
        metadata = getattr(env, "metadata", None)
        if metadata is not None:
            check("environment", "configuration", source.environment, metadata)
        observation = point.observation = env.reset(source.seed)
        compare_observation("reset", observation, source.initial_observation,
                            source.initial_facts, source.initial_result)
        if token_limit is not None and used_tokens is None:
            report["warnings"].append("Source prefix inference usage is unknown")
        if source.initial_state_token is not None:
            check("reset", "state_token", source.initial_state_token, env.state_token())
        for number, step in enumerate(source.steps[:index], 1):
            if is_terminal(observation):
                report["status"] = "early_terminal"
                raise ValueError("Prefix reached a terminal state before the replay point")
            if step.facts_before:
                check(f"before_action_{number}", "visible_facts", step.facts_before,
                      visible_facts(source.benchmark, observation))
            report["replay_step_calls"] += 1
            observation = point.observation = env.step(step.action)
            report["replayed_steps"] += 1
            compare_observation(f"after_action_{number}", observation, step.observation,
                                step.facts_after, step.result)
            if step.state_token_after is not None:
                check(f"after_action_{number}", "state_token", step.state_token_after, env.state_token())
            point.history = source.steps[:number]
        if is_terminal(observation):
            report["status"] = "early_terminal"
            raise ValueError("Replay point is already terminal")
        expected_facts = source.steps[index].facts_before
        if expected_facts:
            check(f"before_action_{t}", "visible_facts", expected_facts,
                  visible_facts(source.benchmark, observation))
        if remaining["environment_steps"] is not None and remaining["environment_steps"] <= 0:
            report["status"] = "budget_exhausted"
            raise ValueError("No environment step budget remains at the replay point")
        if remaining["inference_tokens"] is not None and remaining["inference_tokens"] <= 0:
            report["status"] = "budget_exhausted"
            raise ValueError("No inference budget remains at the replay point")
        token = env.state_token()
        source_token = source.initial_state_token if index == 0 else source.steps[index - 1].state_token_after
        exact = bool(getattr(env, "restoration", "observational") == "exact" and token and source_token)
        if exact:
            check(f"before_action_{t}", "complete_state_token", source_token, token)
        report["restoration"] = "exact" if exact else "observational"
        if not exact:
            report["warnings"].append("Matching observations do not certify hidden state equality")
        if step_limit is None:
            report["warnings"].append("Source environment step budget is unknown")
        report["checkpoint"] = token if exact else digest({"facts": visible_facts(source.benchmark, observation),
                                                           "prefix": [s.action for s in point.history]})
        report.update(status="ready", ready=True)
    except Exception as exc:
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        try:
            point.close()
        except Exception as cleanup:
            report["errors"].append({"phase": "close", "type": type(cleanup).__name__, "message": str(cleanup)})
    report["observation"] = asdict(point.observation) if point.observation is not None else None
    report["actor_history"]["steps"] = [asdict(step) for step in point.history]
    return point
