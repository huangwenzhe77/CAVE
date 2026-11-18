from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cave.benchmarks import AlfworldEnvironment, WebshopEnvironment
from cave.cli import main
from cave.demo import FixtureEnvironment, fixture_factory
from cave.io import append_trajectory, load_trajectories, write_json_exclusive
from cave.llm import ChatActor, ChatClient
from cave.m1_demo import ScriptedActor, run_demo
from cave.restore import restore_before
from cave.runtime import Observation, Proposal, collect_attempt
from cave.schema import Trajectory


class TrackingEnvironment(FixtureEnvironment):
    def __init__(self, benchmark="alfworld", config=None):
        super().__init__(benchmark, config or {"fixture": "cave-protocol-v1", "benchmark_results": False})
        self.actions, self.seeds, self.closed = [], [], False

    def reset(self, seed):
        self.seeds.append(seed)
        return super().reset(seed)

    def step(self, action):
        self.actions.append(action)
        return super().step(action)

    def close(self):
        self.closed = True


def source_trace(benchmark="alfworld", actor=None, environment=None, **kwargs):
    env = environment or TrackingEnvironment(benchmark)
    return collect_attempt(env, actor or ScriptedActor(benchmark), benchmark=benchmark,
        task_id="m1-test", task="Fixture task", run_id="test", environment_config=env.metadata,
        max_steps=4, seed=23, constraints=("fixture only",), **kwargs)


class CollectionTests(unittest.TestCase):
    def test_complete_trace_costs_configuration_seed_and_terminal_flags(self):
        class Actor(ScriptedActor):
            def act(self, task, initial, steps, observation, seed):
                return replace(super().act(task, initial, steps, observation, seed),
                               thoughts=("a thought",), inference_tokens=7)

        env = TrackingEnvironment()
        trace = source_trace(actor=Actor("alfworld"), environment=env, token_budget=100)
        self.assertEqual(trace.outcome, "failure")
        self.assertEqual(trace.metrics["inference_tokens"], 14)
        self.assertEqual([s.actor_seed for s in trace.steps], [23, 24])
        self.assertEqual([s.inference_tokens for s in trace.steps], [7, 7])
        self.assertEqual(env.actions, ["open cabinet 1", "look"])
        self.assertTrue(trace.terminal["terminated"])
        self.assertEqual(trace.terminal["score"], 0)
        self.assertEqual(trace.reflections, ())
        self.assertEqual(trace.actor_config["type"], "scripted_fixture")
        self.assertEqual(trace.budget["max_environment_steps"], 4)
        self.assertEqual(trace.constraints, ("fixture only",))
        self.assertTrue(env.closed)
        self.assertEqual(Trajectory.from_dict(json.loads(json.dumps(asdict(trace)))).fingerprint,
                         trace.fingerprint)

    def test_success_is_recorded_from_environment(self):
        class Actor:
            identity = "success-fixture"

            def act(self, task, initial, steps, observation, seed):
                return Proposal("open cabinet 1" if not steps else "take apple 1 from cabinet 1")

        trace = source_trace(actor=Actor())
        self.assertEqual(trace.outcome, "success")
        self.assertEqual(trace.terminal["score"], 1)

    def test_environment_and_actor_errors_preserve_partial_attempts(self):
        class BrokenEnv(TrackingEnvironment):
            def step(self, action):
                if self.calls:
                    raise OSError("environment disconnected")
                return super().step(action)

        trace = source_trace(environment=BrokenEnv())
        self.assertEqual(trace.outcome, "error")
        self.assertEqual(len(trace.steps), 1)
        self.assertEqual(trace.metrics["environment_step_calls"], 2)
        self.assertTrue(trace.error["environment_state_uncertain"])
        self.assertEqual(trace.error["pending_proposal"]["action"], "look")

        class BrokenActor:
            identity = "broken"

            def act(self, *args):
                raise ValueError("malformed response")

        trace = source_trace(actor=BrokenActor())
        self.assertEqual(trace.outcome, "error")
        self.assertEqual(trace.steps, ())
        self.assertFalse(trace.metrics["inference_usage_complete"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traces.jsonl"
            append_trajectory(path, trace)
            self.assertEqual(load_trajectories(path)[trace.id].error["phase"], "actor")

    def test_setup_reset_and_close_errors_are_recorded(self):
        def broken_factory():
            raise ImportError("ALFWorld not installed")

        trace = collect_attempt(broken_factory, ScriptedActor("alfworld"), benchmark="alfworld",
            task_id="test", task="Fixture task", run_id="test", environment_config={})
        self.assertEqual(trace.error["phase"], "environment_setup")
        self.assertEqual(trace.steps, ())

        class BrokenReset(TrackingEnvironment):
            def reset(self, seed):
                raise OSError("cannot reset")

        env = BrokenReset()
        self.assertEqual(source_trace(environment=env).error["phase"], "reset")
        self.assertTrue(env.closed)

        class BrokenClose(TrackingEnvironment):
            def close(self):
                raise OSError("cannot close")

        self.assertEqual(source_trace(environment=BrokenClose()).error["phase"], "close")

    def test_step_and_inference_limits_are_not_hidden(self):
        class WaitingEnv(TrackingEnvironment):
            def step(self, action):
                self.calls += 1
                return Observation("waiting")

        class WaitingActor:
            identity = "waiting"

            def act(self, *args):
                return Proposal("look")

        trace = source_trace(environment=WaitingEnv(), actor=WaitingActor())
        self.assertEqual(trace.outcome, "truncated")
        self.assertEqual(trace.termination_reason, "step_budget_exhausted")
        self.assertEqual(len(trace.steps), 4)

        class ExpensiveActor:
            identity = "expensive"

            def act(self, *args):
                return Proposal("look", inference_tokens=11)

        env = TrackingEnvironment()
        trace = source_trace(environment=env, actor=ExpensiveActor(), token_budget=10)
        self.assertEqual(trace.outcome, "truncated")
        self.assertEqual(trace.termination_reason, "inference_budget_exceeded")
        self.assertEqual(trace.metrics["inference_tokens"], 11)
        self.assertEqual(env.actions, [])
        self.assertEqual(trace.metrics["pending_proposal"]["action"], "look")

    def test_actor_prompt_contains_no_historical_reflection(self):
        class Client:
            identity = "fake-client"

            def complete(self, system, user, seed):
                self.system, self.user = system, json.loads(user)
                return '{"action":"look","thoughts":["check room"]}', 9

        client = Client()
        actor = ChatActor(client, "alfworld")
        result = actor.act("task", "room", (), Observation("room"), 3)
        self.assertNotIn("reflection", client.system.lower())
        self.assertNotIn("reflections", client.user)
        self.assertEqual(result.inference_tokens, 9)
        self.assertFalse(hasattr(actor, "reflect"))

    def test_actor_configuration_does_not_save_api_key(self):
        client = ChatClient({"endpoint": "http://localhost:8000/v1", "model": "test",
                             "api_key": "do-not-store", "model_revision": "abc"})
        self.assertNotIn("do-not-store", json.dumps(ChatActor(client, "webshop").config))

    def test_malformed_response_preserves_known_usage_and_alfworld_thought_is_not_executed(self):
        class Client:
            identity = "bad-output-client"

            def complete(self, *args):
                return self.raw, 13

        for raw in ("invalid JSON", '{"action":"think: inspect the room"}'):
            client = Client()
            client.raw = raw
            env = TrackingEnvironment()
            trace = source_trace(actor=ChatActor(client, "alfworld"), environment=env)
            self.assertEqual(trace.outcome, "error")
            self.assertEqual(trace.metrics["inference_tokens"], 13)
            self.assertTrue(trace.metrics["inference_usage_complete"])
            self.assertEqual(trace.error["raw_actor_response"], raw)
            self.assertEqual(env.actions, [])


class PersistenceTests(unittest.TestCase):
    def test_append_keeps_previous_bytes_and_rejects_duplicate_without_leaving_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traces.jsonl"
            first, second = source_trace(), source_trace()
            self.assertNotEqual(first.id, second.id)
            append_trajectory(path, first)
            previous = path.read_bytes()
            append_trajectory(path, second)
            self.assertTrue(path.read_bytes().startswith(previous))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                append_trajectory(path, first)
            self.assertFalse(path.with_suffix(".jsonl.lock").exists())
            self.assertEqual(len(load_trajectories(path)), 2)

    def test_report_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            write_json_exclusive(path, {"first": True})
            with self.assertRaises(FileExistsError):
                write_json_exclusive(path, {})
            self.assertEqual(json.loads(path.read_text()), {"first": True})


class RestorationTests(unittest.TestCase):
    def test_t_is_one_based_and_only_prefix_executes_with_original_seed(self):
        for benchmark in ("alfworld", "webshop"):
            source = source_trace(benchmark)
            for t in (1, 2):
                env = TrackingEnvironment(benchmark, source.environment)
                with restore_before(source, t, lambda *args: env) as point:
                    self.assertTrue(point.report["ready"], point.report)
                    self.assertEqual(env.actions, [s.action for s in source.steps[:t - 1]])
                    self.assertEqual(env.seeds, [source.seed])
                    self.assertEqual(point.history, source.steps[:t - 1])
                    self.assertEqual(point.report["remaining_budget"]["environment_steps"], 5 - t)
                    self.assertEqual(point.report["new_actor_calls"], 0)
                    self.assertEqual(point.report["restoration"], "exact")
                    self.assertFalse(env.closed)
                self.assertTrue(env.closed)
        for t in (0, 3, True):
            with self.assertRaises(ValueError):
                restore_before(source, t, fixture_factory)

    def test_restored_environment_is_live_and_budget_is_source_budget(self):
        class Actor(ScriptedActor):
            def act(self, *args):
                return replace(super().act(*args), inference_tokens=7)

        source = source_trace(actor=Actor("alfworld"), token_budget=100)
        with restore_before(source, 2, fixture_factory) as point:
            self.assertEqual(point.report["remaining_budget"]["inference_tokens"], 93)
            self.assertEqual(point.report["source_prefix_inference_tokens"], 7)
            self.assertTrue(point.environment.step("take apple 1 from cabinet 1").success)

    def test_initial_prefix_facts_and_state_token_drift_close_environment(self):
        source = source_trace()
        mutations = [replace(source, initial_observation="changed reset"),
                     replace(source, initial_facts={"changed": True}),
                     replace(source, initial_state_token="changed token"),
                     replace(source, steps=(replace(source.steps[0], observation="changed prefix"), source.steps[1])),
                     replace(source, steps=(source.steps[0], replace(source.steps[1], facts_before={"changed": True})))]
        for mutation in mutations:
            env = TrackingEnvironment(config=source.environment)
            point = restore_before(mutation, 2, lambda *args: env)
            self.assertFalse(point.report["ready"])
            self.assertEqual(point.report["status"], "drift")
            self.assertTrue(env.closed)

    def test_early_terminal_and_unknown_budget_are_explicit(self):
        source = source_trace()
        terminal = replace(source, initial_result=None, initial_state_token=None)

        class TerminalEnv(TrackingEnvironment):
            def reset(self, seed):
                return replace(super().reset(seed), terminated=True)

        point = restore_before(terminal, 1, lambda *args: TerminalEnv(config=source.environment))
        self.assertEqual(point.report["status"], "early_terminal")
        self.assertFalse(point.report["ready"])
        legacy = replace(source, budget={}, initial_state_token=None, reflections=("old reflection must not enter actor history",),
                         initial_facts=None, initial_result=None)
        with restore_before(legacy, 1, fixture_factory) as point:
            self.assertTrue(point.report["ready"])
            self.assertIsNone(point.report["remaining_budget"]["environment_steps"])
            self.assertEqual(point.report["restoration"], "observational")
            self.assertTrue(point.report["warnings"])
            self.assertNotIn("old reflection", json.dumps(point.report))

    def test_backend_claim_without_source_state_token_is_not_exact(self):
        source = replace(source_trace(), initial_state_token=None)
        with restore_before(source, 1, fixture_factory) as point:
            self.assertEqual(point.report["restoration"], "observational")

    def test_exhausted_budget_and_backend_errors_fail_explicitly(self):
        source = replace(source_trace(), budget={"max_environment_steps": 1})
        point = restore_before(source, 2, fixture_factory)
        self.assertEqual(point.report["status"], "budget_exhausted")

        def broken_factory(*args):
            raise OSError("service unavailable")

        point = restore_before(source, 1, broken_factory)
        self.assertEqual(point.report["status"], "error")
        self.assertIn("service unavailable", point.report["errors"][0]["message"])


class BenchmarkContractTests(unittest.TestCase):
    def test_alfworld_saves_raw_observation_and_repeated_action_reason(self):
        class NativeEnv:
            def step(self, actions):
                return ["You arrive at loc 1. Nothing happens."], [0], [False], {
                    "won": [False], "admissible_commands": [["look"]]}

        env = object.__new__(AlfworldEnvironment)
        env.env, env.calls, env.max_steps, env.last_action = NativeEnv(), 0, 5, "look"
        observation = env.step("look")
        self.assertEqual(observation.reason, "repeated_action")
        self.assertTrue(observation.truncated)
        self.assertEqual(observation.text, "Nothing happens.")
        self.assertEqual(observation.raw_text, "You arrive at loc 1. Nothing happens.")

    def test_webshop_think_consumes_budget_reset_restores_wrapper_state_and_score_is_strict(self):
        env = WebshopEnvironment({"base_url": "http://fixture.invalid", "session_idx": "0",
                                  "dataset_revision": "fixture-only", "max_steps": 2})

        def page():
            env.last_page = "fixture HTML"
            return "fixture page", 0.5 if env.session["page_type"] == "end" else 0

        env._page = page
        env.reset(7)
        self.assertEqual(env.step("think[inspect]").text, "OK.")
        self.assertTrue(env.step("think[inspect again]").truncated)
        self.assertEqual(env.calls, 2)
        self.assertIsNone(env.reset(7).facts["last_action"])
        self.assertEqual(env.calls, 0)
        env.session = {"page_type": "item"}
        result = env.step("click[Buy Now]")
        self.assertTrue(result.terminated)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "purchase_score_below_one")


class CommandTests(unittest.TestCase):
    def test_setup_error_is_persisted_and_collect_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            task, config, output = folder / "task.json", folder / "llm.json", folder / "traces.jsonl"
            write_json_exclusive(task, {"benchmark": "alfworld", "task_id": "fixture",
                                       "task": "Fixture only", "environment": {}})
            write_json_exclusive(config, {"endpoint": "http://fixture.invalid/v1", "model": "fixture"})
            with patch("cave.cli.create_environment", side_effect=ImportError("ALFWorld unavailable")), \
                    patch("cave.llm.ChatClient.complete", side_effect=AssertionError("LLM must not run")):
                self.assertEqual(main(["collect", "--task", str(task), "--llm-config", str(config),
                                       "--run-id", "error-test", "--attempts", "1", "--output", str(output)]), 1)
            trace = next(iter(load_trajectories(output).values()))
            self.assertEqual(trace.outcome, "error")
            self.assertEqual(trace.metrics["actor_calls"], 0)

    def test_collect_repeated_invocations_append_independent_attempts_without_reflection(self):
        class FakeClient:
            identity = "fake-client"

            def __init__(self, config):
                pass

            def complete(self, system, user, seed):
                return '{"action":"look"}', 3

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            task = folder / "task.json"
            config = folder / "llm.json"
            output = folder / "traces.jsonl"
            write_json_exclusive(task, {"benchmark": "alfworld", "task_id": "fixture",
                                       "task": "Fixture only", "environment": {"fixture": "cave-protocol-v1"}})
            write_json_exclusive(config, {})
            argv = ["collect", "--task", str(task), "--llm-config", str(config), "--run-id", "test",
                    "--attempts", "2", "--output", str(output)]
            with patch("cave.cli.ChatClient", FakeClient), patch("cave.cli.create_environment", fixture_factory):
                self.assertEqual(main(argv), 0)
                self.assertEqual(main(argv), 0)
            traces = list(load_trajectories(output).values())
            self.assertEqual([t.attempt for t in traces], [0, 1, 2, 3])
            self.assertEqual([t.seed for t in traces], [0, 1, 2, 3])
            self.assertTrue(all(not t.reflections for t in traces))
            self.assertTrue(all(t.metrics["actor_calls"] == 1 for t in traces))

    def test_m1_demo_and_restore_cli_do_not_call_models(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_demo(folder)
            self.assertFalse(result["benchmark_results"])
            for sample in result["samples"]:
                output = str(Path(folder) / (sample["source_id"] + ".cli.json"))
                argv = ["restore", "--trajectories", result["trajectories"],
                        "--trajectory-id", sample["source_id"], "--t", "2", "--output", output,
                        "--environment-factory", "cave.demo:fixture_factory"]
                with patch("cave.cli.ChatClient", side_effect=AssertionError("LLM must not run")):
                    self.assertEqual(main(argv), 0)
                report = json.loads(Path(output).read_text())
                self.assertEqual(report["replayed_steps"], 1)
                self.assertEqual(len(report["actor_history"]["steps"]), 1)
                with self.assertRaises(SystemExit):
                    main(argv)


if __name__ == "__main__":
    unittest.main()
