"""ALFWorld M2 candidate contract tests."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cave.cli import main
from cave.io import write_jsonl
from cave.alfworld_candidate_extraction import extraction_request, validate_response
from cave.schema import Step, Trajectory


class M2AlfworldTests(unittest.TestCase):
    def setUp(self):
        before = {"admissible_commands": ["look", "open cabinet 1"],
                  "observation": "You are facing cabinet 1."}
        self.source = Trajectory(
            id="failed-source", benchmark="alfworld", task_id="task-1",
            task="Find an object.", run_id="m1", outcome="truncated",
            steps=(Step("look", "You are facing cabinet 1.", facts_before=before),
                   Step("look", "Nothing happens.", facts_before=before)),
            environment={"gamefile": "/game.tw-pddl"}, initial_observation="Room.",
            termination_reason="repeated_action",
        )
        self.proposal = {
            "t": 1,
            "mechanism": "Repeated observation without new information.",
            "correction": "open cabinet 1",
            "P": [{"path": "admissible_commands", "op": "contains",
                   "value": "open cabinet 1"}],
            "N": [{"path": "not_recorded", "op": "eq", "value": True}],
            "evidence": {"location": "step_observation", "step_t": 2,
                         "quote": "Nothing happens."},
            "validity_limits": ["Only when opening cabinet 1 is admissible."],
            "rationale": "The later observation did not advance the search.",
        }

    def test_source_only_prompt_and_program_owned_provenance(self):
        request = extraction_request(self.source)
        payload = json.loads(request["user"])
        self.assertEqual([row["t"] for row in payload["steps"]], [1, 2])
        self.assertNotIn("successful", payload)
        self.assertNotIn("reflections", payload)
        records = validate_response(self.source, {"candidates": [self.proposal]},
                                    teacher_config={"model": "teacher"}, inference_tokens=12)
        row = records[0]
        self.assertEqual(row["t"], 1)
        self.assertEqual(row["step_index"], 0)
        self.assertEqual(row["original_action"], "look")
        self.assertEqual(row["source_sha256"], self.source.fingerprint)
        self.assertEqual(row["status"], "unverified")
        self.assertEqual(row["predicate_checks"], {"P": [True], "N": [None]})
        self.assertEqual(row["teacher"]["inference_tokens"], 12)

    def test_rejects_predicates_that_do_not_apply_at_source_checkpoint(self):
        bad = {**self.proposal,
               "P": [{"path": "observation", "op": "contains",
                      "value": "A fact from a later step"}]}
        with self.assertRaisesRegex(ValueError, "false at the source checkpoint"):
            validate_response(self.source, {"candidates": [bad]})

    def test_rejects_invalid_source_step_correction_and_evidence(self):
        cases = [
            ({"t": 0}, "1-based"),
            ({"t": 3}, "1-based"),
            ({"source_id": "forged"}, "exactly"),
            ({"correction": "go to cabinet 1"}, "admissible"),
            ({"correction": "look"}, "differ"),
            ({"P": []}, "P must"),
            ({"evidence": {"location": "step_observation", "step_t": 2,
                           "quote": "invented"}}, "absent"),
            ({"evidence": {"location": "step_observation", "step_t": 0,
                           "quote": "Nothing happens."}}, "at or after"),
        ]
        for change, message in cases:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                validate_response(self.source, {"candidates": [{**self.proposal, **change}]})

    def test_terminal_evidence_and_empty_hypotheses(self):
        candidate = {**self.proposal,
                     "evidence": {"location": "termination_reason", "step_t": None,
                                  "quote": "repeated_action"}}
        records = validate_response(self.source, {"candidates": [candidate]})
        self.assertEqual(records[0]["evidence"]["location"], "termination_reason")
        self.assertEqual(validate_response(self.source, {"candidates": []}), [])

    def test_offline_cli_writes_unverified_candidate_and_audit(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "source.jsonl"
            response_path = root / "response.json"
            output_path = root / "candidates.jsonl"
            prompt_path = root / "prompts.jsonl"
            write_jsonl(source_path, [self.source], exclusive=True)
            response_path.write_text(json.dumps({"candidates": [self.proposal]}), encoding="utf-8")
            self.assertEqual(main(["alfworld-candidate-prompts", "--trajectories", str(source_path),
                                   "--output", str(prompt_path)]), 0)
            self.assertEqual(main(["alfworld-candidate-extract", "--trajectories", str(source_path),
                                   "--source-id", self.source.id,
                                   "--response-file", str(response_path),
                                   "--output", str(output_path)]), 0)
            rows = [json.loads(line) for line in output_path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "unverified")
            self.assertTrue(Path(str(output_path) + ".audit.jsonl").exists())
            with self.assertRaisesRegex(SystemExit, "already exists"):
                main(["alfworld-candidate-extract", "--trajectories", str(source_path),
                      "--response-file", str(response_path), "--output", str(output_path)])


if __name__ == "__main__":
    unittest.main()
