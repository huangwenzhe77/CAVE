"""Small protocol fixtures, not ALFWorld/WebShop benchmark implementations."""

from .runtime import Observation, Proposal, collect_attempt
from .schema import Candidate, Predicate, digest


class FixtureEnvironment:
    restoration = "exact"

    def __init__(self, benchmark, config):
        self.benchmark = benchmark
        self.metadata = config
        self.state = {}

    def reset(self, seed):
        self.state = {"open": False, "selected": False, "done": False}
        self.calls = 0
        value = "The cabinet 1 is closed." if self.benchmark == "alfworld" else "Size\n[small]\n[large]\n[Buy Now]"
        return Observation(value, self.facts())

    def facts(self):
        return {"cabinet_open": self.state["open"], "size_selected": self.state["selected"]}

    def step(self, action):
        self.calls += 1
        success, terminated = False, False
        if self.benchmark == "alfworld":
            if action == "open cabinet 1":
                self.state["open"] = True
                value = "You open the cabinet 1. In it, you see an apple 1."
            elif action == "take apple 1 from cabinet 1" and self.state["open"]:
                value, success, terminated = "Task is SOLVED.", True, True
            else:
                value, terminated = "Nothing happens.", True
        else:
            if action == "click[large]":
                self.state["selected"] = True
                value = "You have clicked large."
            elif action == "click[Buy Now]":
                success, terminated = self.state["selected"], True
                value = f"Your score (min 0.0, max 1.0): {float(success)}"
            else:
                value, terminated = "Invalid action!", True
        self.state["done"] = terminated
        return Observation(value, self.facts(), success, terminated, score=float(success))

    def state_token(self):
        return digest({"benchmark": self.benchmark, "state": self.state, "calls": self.calls})

    def close(self):
        pass


class FixtureActor:
    identity = "fixture-state-policy-v1"

    def __init__(self, fail=False):
        self.fail = fail

    def act(self, task, initial, steps, observation, seed, reflections=()):
        if "cabinet_open" in observation.facts and "cabinet" in initial:
            action = "take apple 1 from cabinet 1" if self.fail or observation.facts["cabinet_open"] else "open cabinet 1"
        else:
            action = "click[Buy Now]" if self.fail or observation.facts["size_selected"] else "click[large]"
        return Proposal(action)


def fixture_factory(benchmark, config):
    if config.get("fixture") != "cave-protocol-v1":
        raise ValueError("Fixture backend accepts only explicit protocol fixtures")
    return FixtureEnvironment(benchmark, config)


def demo_inputs():
    trajectories, candidates = {}, []
    for benchmark in ("alfworld", "webshop"):
        settings = {"fixture": "cave-protocol-v1", "benchmark_results": False}
        for fail in (True, False):
            actor = FixtureActor(fail)
            # Source actor identity must match its actual policy; a failed source action
            # can come from a different frozen actor. Replay uses that same policy.
            actor.identity = "fixture-fail-v1" if fail else "fixture-state-policy-v1"
            trajectory = collect_attempt(fixture_factory(benchmark, settings), actor,
                benchmark=benchmark, task_id=benchmark + "-fixture", task=(
                    "Take apple 1 from cabinet 1." if benchmark == "alfworld" else "Buy the large item."),
                run_id="fixture-failed" if fail else "fixture-success", environment_config=settings,
                max_steps=4, constraints=("Use only the specified fixture task.",))
            trajectories[trajectory.id] = trajectory
            if fail:
                source = trajectory
            else:
                contrast = trajectory
        candidates.append(Candidate(source.id, 0,
            "Closed receptacle blocks taking the object." if benchmark == "alfworld" else
            "Purchasing before selecting the required size violates the task.",
            "open cabinet 1" if benchmark == "alfworld" else "click[large]",
            (Predicate("benchmark", "eq", benchmark),), (),
            "Nothing happens." if benchmark == "alfworld" else "Your score (min 0.0, max 1.0): 0.0",
            ("Only the deterministic CAVE protocol fixture; not benchmark evidence.",),
            "Successful contrast suggests a prerequisite action; replay tests the local correction.",
            contrast.id))
    return trajectories, candidates


def replay_actor():
    actor = FixtureActor(fail=True)
    actor.identity = "fixture-fail-v1"
    return actor
