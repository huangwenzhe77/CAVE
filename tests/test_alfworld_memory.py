"""M4 ALFWorld memory assembly tests."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cave.alfworld_memory import build_memory, build_memory_bank
from cave.alfworld_repair import RepairConfig, run_repair
from cave.cli import main
from cave.demo import demo_inputs, fixture_factory, replay_actor
from cave.io import write_jsonl


class AlfworldMemoryAssemblyTests(unittest.TestCase):
    def setUp(self):
        traces, candidates = demo_inputs()
        self.source = next(t for t in traces.values()
                           if t.benchmark == "alfworld" and t.outcome == "failure")
        proposal = candidates[0]
        self.candidate = {
            "id": "fixture-memory-candidate",
            "status": "unverified",
            "source_id": self.source.id,
            "source_sha256": self.source.fingerprint,
            "benchmark": "alfworld",
            "task_id": self.source.task_id,
            "t": 1,
            "step_index": 0,
            "original_action": self.source.steps[0].action,
            "mechanism": proposal.mechanism,
            "correction": proposal.correction,
            "P": [{"path": "benchmark", "op": "eq", "value": "alfworld"}],
            "N": [],
            "evidence": {"location": "step_observation", "step_t": 1,
                         "quote": "Nothing happens."},
            "validity_limits": ["Fixture only."],
            "rationale": proposal.rationale,
        }

    def _records(self, candidate=None):
        return run_repair(
            self.source, candidate or self.candidate, replay_actor(), fixture_factory,
            RepairConfig(repetitions=2, token_budget=100))[0]

    def test_success_assembly_preserves_tuple_and_evidence(self):
        records = self._records()
        memory, summary = build_memory_bank(self.source, self.candidate, records, 2)
        self.assertEqual(memory.q["status"], "verified")
        self.assertTrue(memory.eligible)
        self.assertEqual(memory.a_minus, self.source.steps[0].action)
        self.assertEqual(memory.a_tilde, "open cabinet 1")
        self.assertEqual(len(memory.P), 1)
        self.assertEqual(len(memory.E["repair"]["records"]), 2)
        self.assertEqual(summary["eligible"], 1)
        self.assertEqual(summary["repair_outcomes"], {"success": 2})

    def test_blocked_repair_is_exported_without_admission(self):
        blocked = {**self.candidate,
                   "id": "fixture-blocked-candidate",
                   "P": [{"path": "missing", "op": "eq", "value": True}]}
        records = self._records(blocked)
        memory = build_memory(self.source, blocked, records, 2)
        self.assertEqual(memory.q["status"], "blocked")
        self.assertFalse(memory.eligible)
        self.assertEqual(memory.q["branch_statuses"], {"blocked": 2})

    def test_cli_exports_memory_bank_and_summary(self):
        records = self._records()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "source.jsonl"
            candidate_path = root / "candidates.jsonl"
            repairs_path = root / "repairs.jsonl"
            output_dir = root / "bank"
            write_jsonl(source_path, [self.source], exclusive=True)
            write_jsonl(candidate_path, [self.candidate], exclusive=True)
            write_jsonl(repairs_path, records, exclusive=True)
            result = main([
                "alfworld-memory-build",
                "--trajectories", str(source_path),
                "--candidates", str(candidate_path),
                "--repairs", str(repairs_path),
                "--expected-repetitions", "2",
                "--output-dir", str(output_dir),
            ])
            self.assertEqual(result, 0)
            self.assertTrue((output_dir / "memories.jsonl").exists())
            self.assertTrue((output_dir / "eligible.jsonl").exists())
            summary = json.loads((output_dir / "summary.json").read_text())
            self.assertEqual(summary["statuses"], {"verified": 1})
            self.assertEqual(summary["eligible"], 1)
            memory = json.loads((output_dir / "memories.jsonl").read_text().splitlines()[0])
            self.assertEqual(memory["q"]["status"], "verified")
            self.assertEqual(memory["E"]["baseline"]["reexecuted"], False)
            with self.assertRaisesRegex(SystemExit, "not empty"):
                main([
                    "alfworld-memory-build",
                    "--trajectories", str(source_path),
                    "--candidates", str(candidate_path),
                    "--repairs", str(repairs_path),
                    "--expected-repetitions", "2",
                    "--output-dir", str(output_dir),
                ])


if __name__ == "__main__":
    unittest.main()
