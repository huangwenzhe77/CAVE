import argparse
from collections import Counter
from dataclasses import asdict
import importlib
import json
from pathlib import Path

from .benchmarks import create_environment
from .candidates import extraction_request, propose, validate_candidates
from .expel import parse_trial
from .io import append_trajectory, load_trajectories, read_jsonl, write_json_exclusive, write_jsonl
from .llm import ChatActor, ChatClient
from .replay import EnvironmentReplay, VerificationConfig, build_memory
from .runtime import collect_attempt
from .restore import restore_before


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_bank(output_dir, trajectories, candidates, backend=None, config=None):
    candidates = validate_candidates(candidates, trajectories)
    memories = [build_memory(candidate, trajectories, backend, config) for candidate in candidates]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "trajectories.jsonl", trajectories.values())
    write_jsonl(output / "candidates.jsonl", candidates)
    write_jsonl(output / "memories.jsonl", memories)
    write_jsonl(output / "eligible.jsonl", [m for m in memories if m.eligible])
    summary = {"trajectories": len(trajectories), "memories": len(memories),
               "statuses": dict(Counter(m.q["status"] for m in memories)),
               "eligible": sum(m.eligible for m in memories),
               "benchmark_results": False,
               "note": "Source replay evidence only. See per-memory scope and restoration quality."}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    return summary


def parser():
    root = argparse.ArgumentParser(description="Construct source failure memories for ALFWorld/WebShop")
    commands = root.add_subparsers(dest="command", required=True)
    command = commands.add_parser("import-expel", help="Import JSONL trial exports in ExpeL text formats")
    command.add_argument("--input", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("collect", help="Collect attempts using a configured frozen LLM")
    command.add_argument("--task", required=True, help="Task JSON with benchmark, task_id, task, environment")
    command.add_argument("--llm-config", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument("--attempts", type=int, default=4)
    command.add_argument("--seed", type=int, default=0)
    command = commands.add_parser("restore", help="Reset and replay only actions before 1-based t; no model call")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--trajectory-id", required=True)
    command.add_argument("--t", type=int, required=True)
    command.add_argument("--output", required=True, help="New JSON report; cannot overwrite an existing file")
    command.add_argument("--environment-factory", help="Optional module:callable, including explicit test fixtures")
    command = commands.add_parser("m1-demo", help="Offline M1 collection/restoration fixtures, not benchmarks")
    command.add_argument("--output-dir", default="output/m1")
    for name in ("prompts", "propose"):
        command = commands.add_parser(name)
        command.add_argument("--trajectories", required=True)
        command.add_argument("--output", required=True)
        if name == "propose":
            command.add_argument("--llm-config", required=True)
    command = commands.add_parser("build", help="Validate candidates and optionally run source replays")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--candidates", required=True)
    command.add_argument("--output-dir", required=True)
    command.add_argument("--replay", action="store_true")
    command.add_argument("--llm-config")
    command.add_argument("--verification-config", help="JSON fields of VerificationConfig")
    command.add_argument("--environment-factory", help="Optional module:callable for a certified environment backend")
    command = commands.add_parser("demo", help="Offline protocol fixtures, not benchmark experiments")
    command.add_argument("--output-dir", default="output/demo")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "import-expel":
            trials = [parse_trial(row) for row in read_jsonl(args.input)]
            if len({t.id for t in trials}) != len(trials):
                raise ValueError("Duplicate trajectory ID")
            write_jsonl(args.output, trials, exclusive=True)
            result = {"imported": len(trials), "output": str(Path(args.output).resolve())}
        elif args.command == "collect":
            if args.attempts < 1:
                raise ValueError("attempts must be positive")
            task = read_json(args.task)
            benchmark = task["benchmark"]
            actor = ChatActor(ChatClient(read_json(args.llm_config)), benchmark)
            trials = []
            existing = load_trajectories(args.output) if Path(args.output).exists() else {}
            first_attempt = max((t.attempt for t in existing.values()
                                 if t.run_id == args.run_id and t.benchmark == benchmark
                                 and t.task_id == task["task_id"]), default=-1) + 1
            for offset in range(args.attempts):
                attempt = first_attempt + offset
                trajectory = collect_attempt(lambda: create_environment(benchmark, task["environment"]),
                    actor, benchmark=benchmark,
                    task_id=task["task_id"], task=task["task"], run_id=args.run_id,
                    environment_config=task["environment"],
                    max_steps=int(task["environment"].get("max_steps", 50 if benchmark == "alfworld" else 15)),
                    seed=args.seed + attempt, attempt=attempt, constraints=tuple(task.get("constraints", ())),
                    token_budget=task.get("token_budget"))
                trials.append(trajectory)
                append_trajectory(args.output, trajectory)
                print(f"attempt={attempt} id={trajectory.id} outcome={trajectory.outcome} steps={len(trajectory.steps)}")
            result = {"attempts": len(trials), "outcomes": dict(Counter(t.outcome for t in trials)),
                      "output": str(Path(args.output).resolve())}
            if any(t.outcome == "error" for t in trials):
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
        elif args.command == "restore":
            if Path(args.output).exists():
                raise ValueError("Restore report already exists; choose a new output path")
            trajectories = load_trajectories(args.trajectories)
            factory = create_environment
            if args.environment_factory:
                module, name = args.environment_factory.split(":", 1)
                factory = getattr(importlib.import_module(module), name)
            with restore_before(trajectories[args.trajectory_id], args.t, factory) as point:
                result = point.report
                write_json_exclusive(args.output, result)
            print(json.dumps({"status": result["status"], "restoration": result["restoration"],
                              "replayed_steps": result["replayed_steps"],
                              "output": str(Path(args.output).resolve())}, ensure_ascii=False, indent=2))
            return 0 if result["ready"] else 1
        elif args.command == "m1-demo":
            from .m1_demo import run_demo
            result = run_demo(args.output_dir)
        elif args.command in {"prompts", "propose"}:
            trajectories = load_trajectories(args.trajectories)
            if args.command == "prompts":
                rows = [extraction_request(t, trajectories) for t in trajectories.values()
                        if t.outcome in {"failure", "truncated"}]
            else:
                rows = propose(trajectories, ChatClient(read_json(args.llm_config)))
            write_jsonl(args.output, rows)
            result = {"written": len(rows), "output": str(Path(args.output).resolve())}
        elif args.command == "build":
            trajectories = load_trajectories(args.trajectories)
            candidates = list(read_jsonl(args.candidates))
            backend = None
            if args.replay:
                if not args.llm_config:
                    raise ValueError("--replay requires --llm-config for the same source actor")
                factory = create_environment
                if args.environment_factory:
                    module, name = args.environment_factory.split(":", 1)
                    factory = getattr(importlib.import_module(module), name)
                client = ChatClient(read_json(args.llm_config))
                actors = {benchmark: ChatActor(client, benchmark) for benchmark in ("alfworld", "webshop")}

                class BenchmarkReplay:
                    identity = "reset-prefix-v1:" + client.identity

                    def run(self, source, index, action, seed, config):
                        return EnvironmentReplay(factory, actors[source.benchmark]).run(source, index, action, seed, config)

                backend = BenchmarkReplay()
            elif args.environment_factory:
                raise ValueError("--environment-factory requires --replay")
            config = VerificationConfig(**read_json(args.verification_config)) if args.verification_config else None
            result = save_bank(args.output_dir, trajectories, candidates, backend, config)
        else:
            from .demo import demo_inputs, fixture_factory, replay_actor
            trajectories, candidates = demo_inputs()
            result = save_bank(args.output_dir, trajectories, candidates,
                               EnvironmentReplay(fixture_factory, replay_actor()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, ImportError) as exc:
        raise SystemExit(f"cave: {type(exc).__name__}: {exc}") from exc
