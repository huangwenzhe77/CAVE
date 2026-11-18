"""Reproducible offline protocol acceptance samples using explicitly labeled protocol fixtures."""

from pathlib import Path

from .demo import fixture_factory
from .io import append_trajectory, write_json_exclusive
from .restore import restore_before
from .runtime import Proposal, collect_attempt


class ScriptedActor:
    identity = "offline-protocol-fixture-v1"
    config = {"type": "scripted_fixture", "benchmark_results": False}

    def __init__(self, benchmark):
        self.actions = (("open cabinet 1", "look") if benchmark == "alfworld"
                        else ("click[large]", "click[small]"))

    def act(self, task, initial, steps, observation, seed):
        return Proposal(self.actions[len(steps)])


def run_demo(output_dir):
    output = Path(output_dir)
    rows = []
    for benchmark in ("alfworld", "webshop"):
        config = {"fixture": "cave-protocol-v1", "benchmark_results": False}
        trajectory = collect_attempt(fixture_factory(benchmark, config), ScriptedActor(benchmark),
            benchmark=benchmark, task_id=benchmark + "-offline-fixture",
            task="Take apple 1." if benchmark == "alfworld" else "Buy a large item.",
            run_id="offline-fixture", environment_config=config, max_steps=4, seed=7)
        append_trajectory(output / "trajectories.jsonl", trajectory)
        with restore_before(trajectory, 2, fixture_factory) as point:
            report = {**point.report, "benchmark_results": False, "fixture": "cave-protocol-v1"}
            report_path = output / (trajectory.id + ".restore.json")
            write_json_exclusive(report_path, report)
            if not point.report["ready"]:
                raise RuntimeError(f"Offline protocol fixture restore failed: {report_path}")
        rows.append({"benchmark": benchmark, "source_id": trajectory.id,
                     "source_outcome": trajectory.outcome, "t": 2, "replayed_steps": 1,
                     "report": str(report_path.resolve())})
    return {"benchmark_results": False, "samples": rows,
            "trajectories": str((output / "trajectories.jsonl").resolve())}
