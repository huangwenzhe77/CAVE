from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from cave.candidates import extraction_request, propose, validate_candidates
from cave.cli import main, save_bank
from cave.demo import demo_inputs, fixture_factory, replay_actor
from cave.expel import parse_trial
from cave.io import load_trajectories, read_jsonl, write_jsonl
from cave.replay import EnvironmentReplay, ReplayOutcome, VerificationConfig, build_memory
from cave.schema import Candidate, Predicate, Step, Trajectory


ROOT = Path(__file__).resolve().parents[1]


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = list(read_jsonl(ROOT / "examples/expel-trials.jsonl"))

    def test_alfworld_thoughts_and_multiline_observations(self):
        row = dict(self.rows[1])
        row["trajectory"] = row["trajectory"].replace("In it,", "\nIn it,")
        trial = parse_trial(row)
        self.assertEqual([s.action for s in trial.steps], ["open cabinet 1", "take apple 1 from cabinet 1"])
        self.assertIn("\nIn it,", trial.steps[0].observation)
        self.assertEqual(trial.steps[0].thoughts, ("Open the cabinet first.",))
        self.assertEqual(trial.raw_text, row["trajectory"])
        self.assertEqual(trial.reflections, tuple(row["reflections"]))

    def test_webshop_think_is_kept_as_an_environment_step(self):
        trial = parse_trial(self.rows[2])
        self.assertEqual(len(trial.steps), 2)
        self.assertEqual(trial.steps[0].action, "think[I found the item.]")
        self.assertEqual(trial.steps[0].observation, "OK.")
        self.assertIn("0.0", trial.steps[1].observation)

    def test_missing_outcome_is_not_inferred_from_text(self):
        row = dict(self.rows[1])
        row.pop("outcome")
        with self.assertRaises(TypeError):
            parse_trial(row)
        trial = parse_trial({**row, "outcome": "unknown"})
        self.assertEqual(trial.outcome, "unknown")

    def test_json_roundtrip_preserves_fingerprints(self):
        original = parse_trial(self.rows[0])
        restored = Trajectory.from_dict(json.loads(json.dumps(asdict(original))))
        self.assertEqual(original.fingerprint, restored.fingerprint)

    def test_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "traces.jsonl"
            trial = parse_trial(self.rows[0])
            write_jsonl(path, [trial, trial])
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_trajectories(path)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.traces, self.candidates = demo_inputs()
        self.candidate = self.candidates[0]

    def test_predicate_unknown_and_exclusion_absence(self):
        self.assertIsNone(Predicate("missing.key", "eq", False).evaluate({}))
        self.assertFalse(Predicate("missing.key", "exists", True).evaluate({}))
        self.assertTrue(Predicate("missing.key", "exists", False).evaluate({}))
        with self.assertRaises(ValueError):
            Predicate("observation", "eval", "arbitrary code")

    def test_source_step_signature_and_outcome_validation(self):
        for changes in ({"step_index": 100}, {"failure_signature": "invented failure"},
                        {"source_id": self.candidate.contrast_id},
                        {"correction": "take apple 1 from cabinet 1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.candidate, **changes).validate_source(self.traces)

    def test_cross_task_contrasts_are_rejected(self):
        success = self.traces[self.candidate.contrast_id]
        self.traces[success.id] = replace(success, task_id="different")
        with self.assertRaisesRegex(ValueError, "Contrast"):
            self.candidate.validate_source(self.traces)

    def test_duplicate_candidates_are_collapsed_and_count_is_bounded(self):
        self.assertEqual(len(validate_candidates([self.candidate] * 2, self.traces)), 1)
        candidates = [replace(self.candidate, mechanism=f"Hypothesis {index}") for index in range(4)]
        with self.assertRaisesRegex(ValueError, "At most"):
            validate_candidates(candidates, self.traces)

    def test_no_success_contrast_is_required_to_form_a_hypothesis(self):
        source = self.traces[self.candidate.source_id]
        request = extraction_request(source, {source.id: source})
        self.assertEqual(json.loads(request["user"])["successful"], [])
        replace(self.candidate, contrast_id=None).validate_source({source.id: source})

    def test_llm_response_cannot_reference_a_different_source(self):
        candidate = asdict(self.candidate)

        class BadClient:
            def complete(self, *args):
                return json.dumps({"candidates": [{**candidate, "source_id": "invented"}]}), 10

        with self.assertRaisesRegex(ValueError, "different source"):
            propose(self.traces, BadClient())


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.traces, self.candidates = demo_inputs()
        self.backend = EnvironmentReplay(fixture_factory, replay_actor())
        self.config = VerificationConfig()

    def memory(self, candidate=None, backend=None, config=None):
        return build_memory(candidate or self.candidates[0], self.traces,
                            backend or self.backend, config or self.config)

    def test_both_benchmark_grammars_produce_complete_verified_memories(self):
        for candidate in self.candidates:
            memory = self.memory(candidate)
            self.assertTrue(memory.eligible)
            self.assertEqual(memory.q["completed_pairs"], 32)
            self.assertEqual(memory.q["failure_reproductions"], 32)
            self.assertGreater(memory.q["confidence_interval"][0], 0)
            pair = memory.E["pairs"][0]
            self.assertEqual(pair["baseline"]["checkpoint"], pair["repair"]["checkpoint"])
            self.assertEqual(pair["baseline"]["action"], memory.a_minus)
            self.assertEqual(pair["repair"]["action"], memory.a_tilde)
            self.assertIn("fixture", memory.p_minus["validity_limits"][0])

    def test_no_replay_never_becomes_eligible(self):
        memory = build_memory(self.candidates[0], self.traces)
        self.assertFalse(memory.eligible)
        self.assertEqual(memory.q["reasons"], ["no_replay_backend"])

    def test_observation_matched_restore_is_not_an_exact_restore(self):
        original = self.backend

        class Observational:
            identity = "observational-only"

            def run(self, *args):
                return replace(original.run(*args), restoration="observational")

        memory = self.memory(backend=Observational())
        self.assertEqual(memory.q["status"], "unverified")
        self.assertIn("restoration_not_certified_exact", memory.q["reasons"])

    def test_unknown_or_false_predicates_and_true_exclusions_block_eligibility(self):
        for changes in ({"P": (Predicate("missing", "eq", True),)},
                        {"P": (Predicate("benchmark", "eq", "webshop"),)},
                        {"N": (Predicate("benchmark", "eq", "alfworld"),)}):
            memory = self.memory(replace(self.candidates[0], **changes))
            self.assertFalse(memory.eligible)
            self.assertIn("source_predicates_false_or_unknown", memory.q["reasons"])

    def test_restore_drift_blocks_verification(self):
        source = self.traces[self.candidates[0].source_id]
        self.traces[source.id] = replace(source, initial_observation="changed world")
        memory = self.memory()
        self.assertFalse(memory.eligible)
        self.assertEqual(len(memory.E["errors"]), 32)
        self.assertIsNone(memory.q["delta_src"])

    def test_actor_mismatch_cannot_be_verified(self):
        source = self.traces[self.candidates[0].source_id]
        self.traces[source.id] = replace(source, actor="another-actor")
        memory = self.memory()
        self.assertFalse(memory.eligible)
        self.assertIn("actor differs", memory.E["errors"][0]["error"])

    def test_one_broken_pair_cannot_be_dropped_to_certify_the_rest(self):
        original = self.backend

        class BrokenPair:
            identity = "one-error"

            def run(self, source, index, action, seed, config):
                if seed == config.seed_start:
                    raise RuntimeError("restore failed")
                return original.run(source, index, action, seed, config)

        memory = self.memory(backend=BrokenPair())
        self.assertEqual(memory.q["completed_pairs"], 31)
        self.assertEqual(memory.q["status"], "unverified")
        self.assertIsNone(memory.q["confidence_interval"])

    def test_checkpoint_mismatch_and_bad_oracles_are_rejected(self):
        original = self.backend
        candidate = self.candidates[0]
        for mutation in (lambda result: replace(result, checkpoint="different"),
                         lambda result: replace(result, checks={"success": False}),
                         lambda result: replace(result, tool_cost=float("nan"))):
            class Invalid:
                identity = "invalid"

                def run(self, *args):
                    result = original.run(*args)
                    return mutation(result) if result.action == candidate.correction else result

            self.assertFalse(self.memory(backend=Invalid()).eligible)

    def test_ineffective_correction_is_rejected(self):
        memory = self.memory(replace(self.candidates[0], correction="look"))
        self.assertEqual(memory.q["status"], "rejected")
        self.assertLessEqual(memory.q["confidence_interval"][0], 0)

    def test_no_failure_reproduction_is_not_verified(self):
        original = self.backend

        class NoSignature:
            identity = "no-signature"

            def run(self, *args):
                result = original.run(*args)
                return replace(result, observations=("unrelated failure",)) if not result.success else result

        memory = self.memory(backend=NoSignature())
        self.assertIn("source_failure_not_reproduced", memory.q["reasons"])
        self.assertFalse(memory.eligible)

    def test_utility_includes_extra_tool_cost(self):
        memory = self.memory()
        pair = memory.E["pairs"][0]
        self.assertAlmostEqual(pair["delta"], 1 - self.config.lambda_tool / self.config.continuation_budget)

    def test_invalid_config_is_rejected(self):
        for changes in ({"pairs": 0}, {"alpha": 0}, {"min_pairs": 1},
                        {"lambda_tool": -1}, {"lambda_inference": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, **changes)


class EndToEndTests(unittest.TestCase):
    def test_bank_deduplicates_and_separates_unverified(self):
        traces, candidates = demo_inputs()
        with tempfile.TemporaryDirectory() as folder:
            summary = save_bank(folder, traces, candidates + candidates)
            self.assertEqual(summary["memories"], 2)
            self.assertEqual(summary["eligible"], 0)
            self.assertEqual(list(read_jsonl(Path(folder) / "eligible.jsonl")), [])
            memories = list(read_jsonl(Path(folder) / "memories.jsonl"))
            self.assertEqual(set(memories[0]), {"id", "p_minus", "a_minus", "f", "a_tilde", "P", "N", "E", "q"})

    def test_import_prompt_and_build_cli_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            traces = str(folder / "traces.jsonl")
            self.assertEqual(main(["import-expel", "--input", str(ROOT / "examples/expel-trials.jsonl"),
                                   "--output", traces]), 0)
            self.assertEqual(main(["prompts", "--trajectories", traces,
                                   "--output", str(folder / "prompts.jsonl")]), 0)
            self.assertEqual(main(["build", "--trajectories", traces,
                                   "--candidates", str(ROOT / "examples/candidates.jsonl"),
                                   "--output-dir", str(folder / "bank")]), 0)
            result = json.loads((folder / "bank/summary.json").read_text())
            self.assertEqual(result["statuses"], {"unverified": 2})


if __name__ == "__main__":
    unittest.main()
