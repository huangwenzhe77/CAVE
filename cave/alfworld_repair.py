from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from time import perf_counter

from .restore import restore_before
from .runtime import is_terminal, observation_result, visible_facts
from .schema import Predicate, Step, Trajectory, digest, text


@dataclass(frozen=True)
class RepairConfig:
    repetitions: int = 2
    token_budget: int = 20000
    actor_seed_stride: int = 1000

    def __post_init__(self):
        if type(self.repetitions) is not int or self.repetitions < 1:
            raise ValueError("repetitions must be a positive integer")
        if type(self.token_budget) is not int or self.token_budget < 1:
            raise ValueError("token_budget must be a positive integer")
        if type(self.actor_seed_stride) is not int or self.actor_seed_stride < 1:
            raise ValueError("actor_seed_stride must be a positive integer")


def candidate_position(source: Trajectory, candidate: dict) -> dict:
    t = candidate["t"]
    length = len(source.steps)
    distance = length - t
    return {
        "source_trajectory_length": length,
        "t": t,
        "distance_to_last_recorded_step": distance,
        "is_last_recorded_step": distance == 0,
        "is_immediately_before_last_recorded_step": distance == 1,
        "source_termination_reason": source.termination_reason,
    }


def _predicates(candidate: dict, name: str) -> tuple[Predicate, ...]:
    rows = candidate.get(name)
    if not isinstance(rows, list):
        raise ValueError(f"candidate.{name} must be a list")
    return tuple(Predicate(**row) for row in rows)


def validate_repair_candidate(source: Trajectory, candidate: dict) -> tuple[tuple[Predicate, ...], tuple[Predicate, ...]]:
    if source.benchmark != "alfworld":
        raise ValueError("M3 currently supports ALFWorld only")
    if source.outcome not in {"failure", "truncated"}:
        raise ValueError("M3 source must be a failure or truncation")
    if not isinstance(candidate, dict):
        raise ValueError("Candidate must be an object")
    if candidate.get("source_id") != source.id:
        raise ValueError("Candidate source_id does not match the source trajectory")
    if candidate.get("source_sha256") != source.fingerprint:
        raise ValueError("Candidate source_sha256 does not match the source trajectory")
    if candidate.get("status") != "unverified":
        raise ValueError("M3 accepts only unverified M2 candidates")
    if candidate.get("benchmark") != "alfworld":
        raise ValueError("M3 candidate benchmark must be alfworld")
    t = candidate.get("t")
    if type(t) is not int or not 1 <= t <= len(source.steps):
        raise ValueError("Candidate t must be a recorded 1-based action number")
    if candidate.get("step_index") != t - 1:
        raise ValueError("Candidate step_index must equal t - 1")
    source_step = source.steps[t - 1]
    if candidate.get("original_action") != source_step.action:
        raise ValueError("Candidate original_action does not match the source step")
    correction = text(candidate.get("correction"), "candidate.correction")
    allowed = source_step.facts_before.get("admissible_commands")
    if isinstance(allowed, list) and correction not in allowed:
        raise ValueError("Candidate correction is not admissible at the source checkpoint")
    if correction == source_step.action:
        raise ValueError("Candidate correction must differ from the source action")
    p = _predicates(candidate, "P")
    n = _predicates(candidate, "N")
    if not p:
        raise ValueError("Candidate P must not be empty")
    return p, n


def _step(action, observation, benchmark, facts_before, thoughts=(), inference_tokens=0,
          actor_seed=None, environment=None) -> Step:
    return Step(action=action, observation=observation.text, thoughts=tuple(thoughts),
                facts_before=deepcopy(facts_before), inference_tokens=inference_tokens,
                actor_seed=actor_seed, facts_after=visible_facts(benchmark, observation),
                result=observation_result(observation),
                state_token_after=environment.state_token() if environment is not None else None)


def _source_baseline(source: Trajectory) -> dict:
    return {
        "trajectory_id": source.id,
        "trajectory_sha256": source.fingerprint,
        "outcome": source.outcome,
        "termination_reason": source.termination_reason,
        "trajectory_length": len(source.steps),
        "metrics": deepcopy(source.metrics),
        "terminal": deepcopy(source.terminal),
        "reexecuted": False,
    }


def _base_record(source, candidate, config, repetition):
    return {
        "id": digest({"source": source.fingerprint, "candidate": candidate.get("id"),
                       "repetition": repetition}),
        "status": "error",
        "benchmark": "alfworld",
        "source_id": source.id,
        "source_sha256": source.fingerprint,
        "candidate_id": candidate.get("id"),
        "candidate_sha256": digest(candidate),
        "task_id": source.task_id,
        "task": source.task,
        "run_id": source.run_id,
        "repetition": repetition,
        "t": candidate["t"],
        "original_action": candidate["original_action"],
        "correction": candidate["correction"],
        "candidate_position": candidate_position(source, candidate),
        "source_baseline": _source_baseline(source),
        "actor": source.actor,
        "actor_config": deepcopy(source.actor_config),
        "repair_config": asdict(config),
        "restoration": None,
        "precondition_status": "not_checked",
        "P_results": None,
        "N_results": None,
        "steps": [],
        "terminal": {},
        "termination_reason": "not_started",
        "error": None,
        "pending_proposal": None,
        "cost": {
            "recovery_environment_steps": 0,
            "correction_environment_steps": 0,
            "continuation_environment_steps": 0,
            "trajectory_length": 0,
            "branch_trajectory_length": 0,
            "actor_calls": 0,
            "inference_tokens": 0,
            "inference_usage_complete": True,
            "remaining_environment_steps": None,
            "normalized_recovery_step_cost": None,
            "normalized_branch_step_cost": None,
            "normalized_inference_cost": 0.0,
        },
        "evidence": {
            "protocol": "source-repair-branch-v1",
            "baseline_reexecuted": False,
            "scope": "This is evidence for this source task and actor configuration only.",
        },
    }


def run_repair_attempt(source: Trajectory, candidate: dict, actor, factory, config: RepairConfig,
                       repetition: int) -> dict:
    p, n = validate_repair_candidate(source, candidate)
    record = _base_record(source, candidate, config, repetition)
    started = perf_counter()
    history = []
    branch_steps = 0
    actor_calls = 0
    inference_tokens = 0
    pending = None
    prefix_steps = 0
    remaining = None
    observation = None
    try:
        with restore_before(source, candidate["t"], factory) as point:
            record["restoration"] = deepcopy(point.report)
            history = list(point.history)
            prefix_steps = point.report.get("replayed_steps", 0)
            remaining = point.report.get("remaining_budget", {}).get("environment_steps")
            record["cost"]["recovery_environment_steps"] = prefix_steps
            record["cost"]["remaining_environment_steps"] = remaining
            if not point.report.get("ready"):
                record["status"] = "blocked"
                record["termination_reason"] = "restoration_failed"
                record["error"] = {"type": "RestorationError",
                                   "message": "M1 restore did not produce a ready checkpoint",
                                   "details": point.report.get("errors", [])}
                return _finish(record, source, history, observation, branch_steps,
                                actor_calls, inference_tokens, pending, prefix_steps,
                                remaining, started)
            observation = point.observation
            facts = visible_facts(source.benchmark, observation)
            record["P_results"] = [predicate.evaluate(facts) for predicate in p]
            record["N_results"] = [predicate.evaluate(facts) for predicate in n]
            p_ok = all(value is True for value in record["P_results"])
            n_ok = all(value is False for value in record["N_results"])
            if not p_ok or not n_ok:
                record["status"] = "blocked"
                record["precondition_status"] = "blocked"
                record["termination_reason"] = "predicate_conditions_not_satisfied"
                record["error"] = {
                    "type": "PredicateGate",
                    "message": "P must be true and N must be false at the restored checkpoint",
                }
                history = list(point.history)
                return _finish(record, source, history, observation, branch_steps,
                                actor_calls, inference_tokens, pending, prefix_steps,
                                remaining, started)
            record["precondition_status"] = "passed"
            if remaining is None:
                record["status"] = "blocked"
                record["termination_reason"] = "unknown_environment_budget"
                record["error"] = {"type": "BudgetError",
                                   "message": "M3 requires a recorded environment step budget"}
                history = list(point.history)
                return _finish(record, source, history, observation, branch_steps,
                                actor_calls, inference_tokens, pending, prefix_steps,
                                remaining, started)
            if remaining < 1:
                record["status"] = "blocked"
                record["termination_reason"] = "no_remaining_environment_steps"
                record["error"] = {"type": "BudgetError",
                                   "message": "No environment step remains for the correction"}
                history = list(point.history)
                return _finish(record, source, history, observation, branch_steps,
                                actor_calls, inference_tokens, pending, prefix_steps,
                                remaining, started)
            history = list(point.history)
            correction_facts = visible_facts(source.benchmark, observation)
            observation = point.environment.step(candidate["correction"])
            branch_steps += 1
            correction_step = _step(candidate["correction"], observation, source.benchmark,
                                    correction_facts, environment=point.environment)
            history.append(correction_step)
            if is_terminal(observation):
                record["termination_reason"] = observation.reason or (
                    "environment_success" if observation.success else "environment_terminal")
            else:
                continuation_limit = max(0, remaining - 1)
                while not is_terminal(observation) and actor_calls < continuation_limit:
                    if inference_tokens >= config.token_budget:
                        record["termination_reason"] = "inference_budget_exhausted"
                        break
                    actor_seed = source.seed + repetition * config.actor_seed_stride + actor_calls
                    actor_calls += 1
                    proposal = actor.act(source.task, source.initial_observation,
                                         tuple(history), observation, actor_seed)
                    inference_tokens += proposal.inference_tokens
                    pending = {"action": proposal.action, "thoughts": proposal.thoughts,
                               "inference_tokens": proposal.inference_tokens,
                               "actor_seed": actor_seed}
                    if inference_tokens > config.token_budget:
                        record["termination_reason"] = "inference_budget_exceeded"
                        break
                    facts_before = visible_facts(source.benchmark, observation)
                    observation = point.environment.step(proposal.action)
                    branch_steps += 1
                    history.append(_step(proposal.action, observation, source.benchmark,
                                         facts_before, proposal.thoughts,
                                         proposal.inference_tokens, actor_seed,
                                         point.environment))
                    pending = None
                if not record["termination_reason"] or record["termination_reason"] == "not_started":
                    record["termination_reason"] = observation.reason or (
                        "environment_success" if observation.success else
                        "step_budget_exhausted" if branch_steps >= remaining else
                        "continuation_stopped")
            record["status"] = "success" if observation.success else "failure"
            return _finish(record, source, history, observation, branch_steps,
                            actor_calls, inference_tokens, pending, prefix_steps,
                            remaining, started)
    except Exception as exc:
        record["status"] = "error"
        record["termination_reason"] = "repair_exception"
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        return _finish(record, source, history, observation, branch_steps,
                        actor_calls, inference_tokens, pending, prefix_steps,
                        remaining, started)


def _finish(record, source, history, observation, branch_steps, actor_calls,
            inference_tokens, pending, prefix_steps, remaining, started):
    record["steps"] = [asdict(step) for step in history]
    record["terminal"] = observation_result(observation) if observation is not None else {}
    record["pending_proposal"] = pending
    record["cost"].update({
        "correction_environment_steps": min(1, branch_steps),
        "continuation_environment_steps": max(0, branch_steps - 1),
        "trajectory_length": len(history),
        "branch_trajectory_length": branch_steps,
        "actor_calls": actor_calls,
        "inference_tokens": inference_tokens,
        "inference_usage_complete": pending is None,
        "normalized_recovery_step_cost": (
            None if source.budget.get("max_environment_steps") in (None, 0)
            else prefix_steps / source.budget["max_environment_steps"]),
        "normalized_branch_step_cost": (
            None if remaining in (None, 0) else branch_steps / remaining),
        "normalized_inference_cost": inference_tokens / record["repair_config"]["token_budget"],
    })
    record["evidence"]["source_failure_comparison"] = {
        "baseline_outcome": source.outcome,
        "repair_outcome": record["status"],
        "baseline_termination_reason": source.termination_reason,
        "repair_termination_reason": record["termination_reason"],
        "baseline_reexecuted": False,
    }
    record["elapsed_seconds"] = perf_counter() - started
    return record


def run_repair(source: Trajectory, candidate: dict, actor, factory, config: RepairConfig) -> tuple[list[dict], dict]:
    validate_repair_candidate(source, candidate)
    if actor.identity != source.actor:
        raise ValueError("Repair actor identity differs from source actor")
    records = [run_repair_attempt(source, candidate, actor, factory, config, repetition)
               for repetition in range(config.repetitions)]
    statuses = Counter(record["status"] for record in records)
    gate = Counter(record["precondition_status"] for record in records)
    costs = [record["cost"] for record in records]
    summary = {
        "protocol": "source-repair-branch-v1",
        "source_id": source.id,
        "source_sha256": source.fingerprint,
        "candidate_id": candidate.get("id"),
        "candidate_position": candidate_position(source, candidate),
        "repetitions": config.repetitions,
        "outcomes": dict(statuses),
        "predicate_gate": dict(gate),
        "costs": {
            "per_repetition": costs,
            "total_inference_tokens": sum(row["inference_tokens"] for row in costs),
            "total_recovery_environment_steps": sum(row["recovery_environment_steps"] for row in costs),
            "total_correction_environment_steps": sum(row["correction_environment_steps"] for row in costs),
            "total_continuation_environment_steps": sum(row["continuation_environment_steps"] for row in costs),
            "total_branch_environment_steps": sum(row["branch_trajectory_length"] for row in costs),
        },
        "comparison": {
            "baseline_outcome": source.outcome,
            "baseline_termination_reason": source.termination_reason,
            "repair_successes": statuses.get("success", 0),
            "repair_failures": statuses.get("failure", 0),
            "blocked": statuses.get("blocked", 0),
            "errors": statuses.get("error", 0),
            "baseline_reexecuted": False,
        },
        "scope": "K repeated repair branches for one source failure; no cross-task claim.",
    }
    return records, summary
