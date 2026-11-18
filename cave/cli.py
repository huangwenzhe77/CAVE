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
from .alfworld_candidate_extraction import (extraction_request as alfworld_candidate_request,
                                               parse_teacher_response as parse_alfworld_teacher_response,
                                               selection_statistics,
                                               validate_response as validate_alfworld_candidate)
from .alfworld_repair import RepairConfig, run_repair
from .alfworld_memory import build_memory_bank
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
    command.add_argument("--trace", action="store_true", help="Print each Thought/Action/Observation as it completes")
    command = commands.add_parser("restore", help="Reset and replay only actions before 1-based t; no model call")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--trajectory-id", required=True)
    command.add_argument("--t", type=int, required=True)
    command.add_argument("--output", required=True, help="New JSON report; cannot overwrite an existing file")
    command.add_argument("--environment-factory", help="Optional module:callable, including explicit test fixtures")
    command = commands.add_parser("offline-protocol-demo", help="Offline M1 collection/restoration fixtures, not benchmarks")
    command.add_argument("--output-dir", default="output/offline-protocol-demo")
    for name in ("prompts", "propose"):
        command = commands.add_parser(name)
        command.add_argument("--trajectories", required=True)
        command.add_argument("--output", required=True)
        if name == "propose":
            command.add_argument("--llm-config", required=True)
    command = commands.add_parser("alfworld-candidate-prompts", help="Export source-only ALFWorld candidate prompts")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--source-id")
    command.add_argument("--output", required=True)
    command = commands.add_parser("alfworld-candidate-extract", help="Extract unverified ALFWorld candidates")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--source-id")
    command.add_argument("--llm-config")
    command.add_argument("--response-file", help="Offline teacher JSON response for one source")
    command.add_argument("--output", required=True)
    command = commands.add_parser("alfworld-repair-verify",
                                  help="Run K ALFWorld repair branches from an M2 candidate")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--candidates", required=True)
    command.add_argument("--candidate-id")
    command.add_argument("--llm-config", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--repetitions", type=int, default=2)
    command.add_argument("--token-budget", type=int, default=20000)
    command.add_argument("--actor-seed-stride", type=int, default=1000)
    command = commands.add_parser("alfworld-memory-build",
                                  help="Assemble an ALFWorld failure memory bank from M1-M3 artifacts")
    command.add_argument("--trajectories", required=True)
    command.add_argument("--candidates", required=True)
    command.add_argument("--repairs", required=True)
    command.add_argument("--candidate-id")
    command.add_argument("--output-dir", required=True)
    command.add_argument("--expected-repetitions", type=int, default=2)
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
                printed_steps = [0]
                if args.trace:
                    print("\nTask: " + task["task"] + f"\nAttempt: {attempt}", flush=True)

                def print_step(step):
                    printed_steps[0] += 1
                    if printed_steps[0] == 1:
                        print("Initial observation: " + step.facts_before.get("observation", ""), flush=True)
                    print(f"\nStep {printed_steps[0]}", flush=True)
                    for thought in step.thoughts:
                        print("Thought: " + thought, flush=True)
                    print("Action: " + step.action, flush=True)
                    print("Observation: " + step.observation, flush=True)
                    print(f"Inference tokens: {step.inference_tokens}", flush=True)

                trajectory = collect_attempt(lambda: create_environment(benchmark, task["environment"]),
                    actor, benchmark=benchmark,
                    task_id=task["task_id"], task=task["task"], run_id=args.run_id,
                    environment_config=task["environment"],
                    max_steps=int(task["environment"].get("max_steps", 50 if benchmark == "alfworld" else 15)),
                    seed=args.seed + attempt, attempt=attempt, constraints=tuple(task.get("constraints", ())),
                    token_budget=task.get("token_budget"),
                    on_step=print_step if args.trace else None)
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
        elif args.command == "offline-protocol-demo":
            from .offline_protocol_demo import run_demo
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
        elif args.command in {"alfworld-candidate-prompts", "alfworld-candidate-extract"}:
            trajectories = load_trajectories(args.trajectories)
            if args.source_id:
                if args.source_id not in trajectories:
                    raise ValueError("Unknown source trajectory ID")
                sources = [trajectories[args.source_id]]
            else:
                sources = [t for t in trajectories.values()
                           if t.benchmark == "alfworld" and t.outcome in {"failure", "truncated"}]
            if not sources:
                raise ValueError("No ALFWorld failure or truncated source trajectories")
            requests = [alfworld_candidate_request(source) for source in sources]
            output = Path(args.output)
            if output.exists():
                raise ValueError("Candidate output already exists; choose a new path")
            if args.command == "alfworld-candidate-prompts":
                write_jsonl(output, requests, exclusive=True)
                result = {"sources": len(sources), "output": str(output.resolve())}
            else:
                if bool(args.llm_config) == bool(args.response_file):
                    raise ValueError("Specify exactly one of --llm-config or --response-file")
                if args.response_file and len(sources) != 1:
                    raise ValueError("Offline response requires exactly one selected source")
                audit_path = Path(str(output) + ".audit.jsonl")
                if audit_path.exists():
                    raise ValueError("M2 audit output already exists; choose a new path")
                client = ChatClient(read_json(args.llm_config)) if args.llm_config else None
                records, audit, selection = [], [], []
                for source, request in zip(sources, requests):
                    if client is not None:
                        response, tokens = client.complete(request["system"], request["user"], source.seed)
                        teacher_config = client.config
                    else:
                        response, tokens = read_json(args.response_file), None
                        teacher_config = {"mode": "offline_response"}
                    proposed = validate_alfworld_candidate(source, response,
                        teacher_config=teacher_config, inference_tokens=tokens)
                    records.extend(proposed)
                    selection_row = selection_statistics(source, proposed)
                    selection.append(selection_row)
                    audit.append({"source_id": source.id, "source_sha256": source.fingerprint,
                                  "prompt_version": request["prompt_version"],
                                  "teacher_config": teacher_config, "inference_tokens": tokens,
                                  "response": parse_alfworld_teacher_response(response) if isinstance(response, str) else response,
                                  "accepted_candidate_ids": [row["id"] for row in proposed],
                                  "selection_statistics": selection_row})
                write_jsonl(output, records, exclusive=True)
                write_jsonl(audit_path, audit, exclusive=True)
                summary_path = Path(str(output) + ".summary.json")
                aggregate = {
                    "sources": len(selection),
                    "candidates": len(records),
                    "last_step_candidates": sum(row["last_step_candidates"] for row in selection),
                    "immediately_before_last_candidates": sum(
                        row["immediately_before_last_candidates"] for row in selection),
                    "earlier_candidates": sum(row["earlier_candidates"] for row in selection),
                }
                write_json_exclusive(summary_path, {"protocol": "candidate-selection-v1",
                                                    "per_source": selection,
                                                    "aggregate": aggregate})
                result = {"sources": len(sources), "candidates": len(records),
                          "status": "unverified", "output": str(output.resolve()),
                          "audit": str(audit_path.resolve()),
                          "selection_summary": str(summary_path.resolve())}
        elif args.command == "alfworld-repair-verify":
            trajectories = load_trajectories(args.trajectories)
            candidates = list(read_jsonl(args.candidates))
            if args.candidate_id:
                candidates = [row for row in candidates if row.get("id") == args.candidate_id]
            if len(candidates) != 1:
                raise ValueError("Repair verification requires exactly one selected candidate")
            candidate = candidates[0]
            source_id = candidate.get("source_id")
            if source_id not in trajectories:
                raise ValueError("Candidate source_id is not present in trajectories")
            if Path(args.output).exists() or Path(str(args.output) + ".summary.json").exists():
                raise ValueError("Repair output already exists; choose new output paths")
            source = trajectories[source_id]
            actor = ChatActor(ChatClient(read_json(args.llm_config)), "alfworld")
            config = RepairConfig(repetitions=args.repetitions, token_budget=args.token_budget,
                                  actor_seed_stride=args.actor_seed_stride)
            records, summary = run_repair(source, candidate, actor, create_environment, config)
            write_jsonl(args.output, records, exclusive=True)
            summary_path = Path(str(args.output) + ".summary.json")
            write_json_exclusive(summary_path, summary)
            result = {"repetitions": len(records), "outcomes": summary["outcomes"],
                      "output": str(Path(args.output).resolve()),
                      "summary": str(summary_path.resolve())}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if summary["outcomes"].get("error", 0) else 0
        elif args.command == "alfworld-memory-build":
            trajectories = load_trajectories(args.trajectories)
            candidates = list(read_jsonl(args.candidates))
            if args.candidate_id:
                candidates = [row for row in candidates if row.get("id") == args.candidate_id]
            if len(candidates) != 1:
                raise ValueError("Memory assembly requires exactly one selected candidate")
            candidate = candidates[0]
            source_id = candidate.get("source_id")
            if source_id not in trajectories:
                raise ValueError("Candidate source_id is not present in trajectories")
            output = Path(args.output_dir)
            if output.exists() and any(output.iterdir()):
                raise ValueError("Memory output directory is not empty; choose a new output directory")
            repair_records = [
                row for row in read_jsonl(args.repairs)
                if row.get("source_id") == source_id and row.get("candidate_id") == candidate.get("id")
            ]
            memory, summary = build_memory_bank(
                trajectories[source_id], candidate, repair_records,
                expected_repetitions=args.expected_repetitions)
            output.mkdir(parents=True, exist_ok=True)
            write_jsonl(output / "candidates.jsonl", [candidate], exclusive=True)
            write_jsonl(output / "memories.jsonl", [memory], exclusive=True)
            write_jsonl(output / "eligible.jsonl", [memory] if memory.eligible else [], exclusive=True)
            write_json_exclusive(output / "summary.json", summary)
            result = {
                **summary,
                "output_dir": str(output.resolve()),
                "memory_output": str((output / "memories.jsonl").resolve()),
                "eligible_output": str((output / "eligible.jsonl").resolve()),
            }
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
