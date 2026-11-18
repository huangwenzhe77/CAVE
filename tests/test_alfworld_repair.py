import unittest

from cave.alfworld_repair import RepairConfig, run_repair
from cave.demo import demo_inputs, fixture_factory, replay_actor


class AlfworldRepairTests(unittest.TestCase):
    def setUp(self):
        traces, candidates = demo_inputs()
        self.source = next(t for t in traces.values() if t.benchmark == "alfworld" and t.outcome == "failure")
        candidate = candidates[0]
        self.candidate = {
            "id": "fixture-candidate",
            "status": "unverified",
            "source_id": self.source.id,
            "source_sha256": self.source.fingerprint,
            "benchmark": "alfworld",
            "task_id": self.source.task_id,
            "t": 1,
            "step_index": 0,
            "original_action": self.source.steps[0].action,
            "correction": candidate.correction,
            "P": [{"path": "benchmark", "op": "eq", "value": "alfworld"}],
            "N": [],
        }

    def test_repair_branch_replays_prefix_without_actor_and_saves_costs(self):
        records, summary = run_repair(
            self.source, self.candidate, replay_actor(), fixture_factory,
            RepairConfig(repetitions=2, token_budget=100))
        self.assertEqual(summary["outcomes"], {"success": 2})
        for record in records:
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["precondition_status"], "passed")
            self.assertEqual(record["source_baseline"]["reexecuted"], False)
            self.assertEqual(record["cost"]["recovery_environment_steps"], 0)
            self.assertEqual(record["cost"]["remaining_environment_steps"], 4)
            self.assertEqual(record["cost"]["correction_environment_steps"], 1)
            self.assertEqual(record["cost"]["continuation_environment_steps"], 1)
            self.assertEqual(record["cost"]["branch_trajectory_length"], 2)
            self.assertEqual(record["cost"]["trajectory_length"], 2)
            self.assertEqual(record["cost"]["actor_calls"], 1)
            self.assertEqual(record["cost"]["inference_tokens"], 0)

    def test_unknown_predicate_blocks_before_correction(self):
        blocked = dict(self.candidate)
        blocked["P"] = [{"path": "missing", "op": "eq", "value": True}]
        records, summary = run_repair(
            self.source, blocked, replay_actor(), fixture_factory,
            RepairConfig(repetitions=2, token_budget=100))
        self.assertEqual(summary["outcomes"], {"blocked": 2})
        for record in records:
            self.assertEqual(record["precondition_status"], "blocked")
            self.assertEqual(record["termination_reason"], "predicate_conditions_not_satisfied")
            self.assertEqual(record["cost"]["correction_environment_steps"], 0)
            self.assertEqual(record["cost"]["actor_calls"], 0)
            self.assertEqual(record["cost"]["trajectory_length"], 0)

    def test_candidate_position_and_budget_are_reported(self):
        records, summary = run_repair(
            self.source, self.candidate, replay_actor(), fixture_factory,
            RepairConfig(repetitions=1, token_budget=100))
        position = summary["candidate_position"]
        self.assertEqual(position["source_trajectory_length"], 1)
        self.assertEqual(position["t"], 1)
        self.assertTrue(position["is_last_recorded_step"])
        self.assertFalse(position["is_immediately_before_last_recorded_step"])
        self.assertEqual(summary["costs"]["total_recovery_environment_steps"], 0)


if __name__ == "__main__":
    unittest.main()
