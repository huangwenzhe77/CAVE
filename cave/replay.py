from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from statistics import mean

from .runtime import Actor, visible_facts
from .schema import Candidate, Memory, Step, Trajectory, digest, finite_nonnegative


@dataclass(frozen=True)
class VerificationConfig:
    pairs: int = 32
    min_pairs: int = 16
    continuation_budget: int = 20
    token_budget: int = 20000
    lambda_inference: float = 0.0
    lambda_tool: float = 0.1
    alpha: float = 0.05
    min_effect: float = 0.0
    seed_start: int = 1000

    def __post_init__(self):
        for name in ("pairs", "min_pairs", "continuation_budget", "token_budget"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.min_pairs < 2 or self.pairs < self.min_pairs:
            raise ValueError("pairs >= min_pairs >= 2 is required")
        if not 0 < self.alpha < 1:
            raise ValueError("alpha must be in (0,1)")
        for name in ("lambda_inference", "lambda_tool", "min_effect"):
            finite_nonnegative(getattr(self, name), name)
        if type(self.seed_start) is not int:
            raise ValueError("seed_start must be an integer")


@dataclass(frozen=True)
class ReplayOutcome:
    action: str
    checkpoint: str
    restoration: str
    success: bool
    inference_cost: float
    tool_cost: float
    observations: tuple[str, ...]
    checks: dict[str, bool]
    facts_before: dict = field(default_factory=dict)
    inference_tokens: int = 0
    tool_calls: int = 0
    prefix_tool_calls: int = 0

    def reward(self, config):
        for name in ("inference_cost", "tool_cost"):
            cost = finite_nonnegative(getattr(self, name), name)
            if cost > 1:
                raise ValueError("Replay costs must be normalized to [0,1]")
        if type(self.success) is not bool or not self.checks:
            raise ValueError("Replay requires a boolean outcome and executable checks")
        if any(type(value) is not bool for value in self.checks.values()):
            raise ValueError("Check outcomes must be boolean")
        if self.success != all(self.checks.values()):
            raise ValueError("Terminal success disagrees with executable checks")
        return (int(self.success) - config.lambda_inference * self.inference_cost
                - config.lambda_tool * self.tool_cost)


class EnvironmentReplay:
    """Fresh environment per arm; replay and check every source prefix observation."""

    def __init__(self, factory, actor: Actor):
        self.factory = factory
        self.actor = actor
        self.identity = "reset-prefix-v1:" + actor.identity

    def run(self, source: Trajectory, index: int, action: str,
            seed: int, config: VerificationConfig) -> ReplayOutcome:
        if self.actor.identity != source.actor:
            raise ValueError("Replay actor differs from the source actor")
        env = self.factory(source.benchmark, source.environment)
        try:
            observation = env.reset(source.seed)
            if observation.text != source.initial_observation:
                raise ValueError("Initial observation differs from source reset")
            prefix = []
            for step in source.steps[:index]:
                if observation.terminated or observation.truncated or observation.success:
                    raise ValueError("Source prefix reaches an early terminal state")
                facts = visible_facts(source.benchmark, observation)
                observation = env.step(step.action)
                if observation.text != step.observation:
                    raise ValueError("Source prefix observation drift")
                prefix.append(Step(step.action, observation.text, step.thoughts, facts))
            if observation.terminated or observation.truncated or observation.success:
                raise ValueError("Candidate checkpoint is already terminal")
            facts = visible_facts(source.benchmark, observation)
            token = env.state_token()
            if env.restoration == "exact" and not token:
                raise ValueError("Exact restoration requires a complete backend state token")
            checkpoint = token or digest({"facts": facts, "actions": [s.action for s in prefix]})
            restoration = "exact" if token and env.restoration == "exact" else "observational"
            observation = env.step(action)
            observations = [observation.text]
            prefix.append(Step(action, observation.text, facts_before=facts))
            inference_tokens, calls = 0, 1
            while not (observation.success or observation.terminated or observation.truncated):
                if calls >= config.continuation_budget or inference_tokens >= config.token_budget:
                    break
                proposal = self.actor.act(source.task, source.initial_observation, tuple(prefix),
                                          observation, seed + calls)
                if proposal.inference_tokens < 0:
                    raise ValueError("Negative inference token count")
                inference_tokens += proposal.inference_tokens
                if inference_tokens > config.token_budget:
                    break
                current_facts = visible_facts(source.benchmark, observation)
                observation = env.step(proposal.action)
                prefix.append(Step(proposal.action, observation.text, proposal.thoughts, current_facts))
                observations.append(observation.text)
                calls += 1
            # The actor never sees terminal evaluation fields in the structured facts.
            return ReplayOutcome(action, checkpoint, restoration, observation.success,
                                 min(1.0, inference_tokens / config.token_budget),
                                 calls / config.continuation_budget, tuple(observations),
                                 {"environment_success": observation.success}, facts,
                                 inference_tokens, calls, index)
        finally:
            env.close()


def build_memory(candidate: Candidate, trajectories: dict[str, Trajectory],
                 backend=None, config: VerificationConfig | None = None) -> Memory:
    config = config or VerificationConfig()
    source = candidate.validate_source(trajectories)
    step = source.steps[candidate.step_index]
    evidence = {"protocol": "source-paired-v1", "config": asdict(config),
                "backend": getattr(backend, "identity", None), "pairs": [], "errors": [],
                "ci_method": "two-sided Hoeffding, fixed K, bounded normalized utility",
                "assumptions": ["independent continuation seeds under a fixed actor",
                                "backend exact state restoration contract",
                                "source replay only; no target benefit claim"]}
    reasons = []
    differences = []
    exact, predicates_ok, reproduced = True, True, 0
    if backend is None:
        reasons.append("no_replay_backend")
    else:
        for seed in range(config.seed_start, config.seed_start + config.pairs):
            try:
                baseline = backend.run(source, candidate.step_index, step.action, seed, config)
                repair = backend.run(source, candidate.step_index, candidate.correction, seed, config)
                if baseline.action != step.action or repair.action != candidate.correction:
                    raise ValueError("Replay executed the wrong intervention action")
                if not baseline.checkpoint or baseline.checkpoint != repair.checkpoint:
                    raise ValueError("Paired arms did not restore the same checkpoint")
                if baseline.facts_before != repair.facts_before:
                    raise ValueError("Paired arms have different pre-action facts")
                delta = repair.reward(config) - baseline.reward(config)
                conditions = [p.evaluate(baseline.facts_before) for p in candidate.P]
                exclusions = [p.evaluate(baseline.facts_before) for p in candidate.N]
                predicates_ok &= all(v is True for v in conditions) and all(v is False for v in exclusions)
                exact &= baseline.restoration == repair.restoration == "exact"
                signature = candidate.failure_signature in baseline.observations[0]
                reproduced += int(not baseline.success and signature)
                differences.append(delta)
                evidence["pairs"].append({"seed": seed, "baseline": asdict(baseline),
                                          "repair": asdict(repair), "delta": delta,
                                          "P_results": conditions, "N_results": exclusions,
                                          "source_failure_reproduced": not baseline.success and signature})
            except Exception as exc:
                # Failed restores/tool calls remain evidence; no successful-pair selection.
                evidence["errors"].append({"seed": seed, "error": f"{type(exc).__name__}: {exc}"})
    status, estimate, interval = "unverified", None, None
    if len(differences) == config.pairs:
        estimate = mean(differences)
        bound = 1 + config.lambda_inference + config.lambda_tool
        radius = 2 * bound * math.sqrt(math.log(2 / config.alpha) / (2 * len(differences)))
        interval = [max(-bound, estimate - radius), min(bound, estimate + radius)]
        if not exact:
            reasons.append("restoration_not_certified_exact")
        if not predicates_ok:
            reasons.append("source_predicates_false_or_unknown")
        if reproduced == 0:
            reasons.append("source_failure_not_reproduced")
        if not reasons:
            status = "verified" if interval[0] > config.min_effect else "rejected"
            if status == "rejected":
                reasons.append("positive_improvement_not_supported")
    elif backend is not None:
        reasons.append("incomplete_paired_evidence")
    quality = {"status": status, "reasons": reasons, "delta_src": estimate,
               "confidence_interval": interval, "confidence_level": 1 - config.alpha,
               "completed_pairs": len(differences), "failure_reproductions": reproduced,
               "scope": {"benchmark": source.benchmark, "environment": source.environment},
               "mechanism_status": "hypothesis_with_replay_support" if status == "verified" else "hypothesis"}
    provenance = {"trajectory_id": source.id, "trajectory_sha256": source.fingerprint,
                  "task_id": source.task_id, "task": source.task, "run_id": source.run_id,
                  "step_index": candidate.step_index, "benchmark": source.benchmark,
                  "environment": source.environment, "actor": source.actor, "seed": source.seed,
                  "constraints": source.constraints, "contrast_id": candidate.contrast_id,
                  "contrast_sha256": trajectories[candidate.contrast_id].fingerprint if candidate.contrast_id else None,
                  "failure_signature": candidate.failure_signature,
                  "validity_limits": candidate.validity_limits, "rationale": candidate.rationale}
    identity = digest({"source": source.fingerprint, "candidate": asdict(candidate)})
    return Memory(identity, provenance, step.action, candidate.mechanism,
                  candidate.correction, candidate.P, candidate.N, evidence, quality)
