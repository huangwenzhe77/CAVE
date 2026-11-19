import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cave.alfworld_data_splits import build_manifest, write_manifest


def make_game(data_root, split, group, trial, task_type="pick_and_place_simple"):
    game_dir = data_root / "json_2.1.1" / split / group / trial
    game_dir.mkdir(parents=True)
    gamefile = game_dir / "game.tw-pddl"
    trajectory = game_dir / "traj_data.json"
    gamefile.write_text(json.dumps({"solvable": True}), encoding="utf-8")
    trajectory.write_text(json.dumps({
        "task_type": task_type,
        "turk_annotations": {"anns": [{"task_desc": "Move an object."}]},
    }), encoding="utf-8")
    relative = gamefile.relative_to(data_root / "json_2.1.1" / split).as_posix()
    return {
        "relative_gamefile": relative,
        "gamefile_sha256": hashlib.sha256(gamefile.read_bytes()).hexdigest(),
    }


class AlfworldDataSplitTests(unittest.TestCase):
    def test_manifest_is_disjoint_and_excludes_prior_validation_smoke_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            source_game = make_game(data_root, "train", "task-source", "trial_1")
            make_game(data_root, "train", "task-source", "trial_2")
            make_game(data_root, "train", "task-target", "trial_1")
            make_game(data_root, "valid_seen", "task-smoke", "trial_1")
            make_game(data_root, "valid_seen", "task-smoke", "trial_2")
            make_game(data_root, "valid_seen", "task-validation", "trial_1")
            make_game(data_root, "valid_unseen", "task-test", "trial_1")

            source_manifest = root / "source.json"
            source_manifest.write_text(json.dumps({
                "eligible_pool_size": 3,
                "count": 1,
                "games": [{
                    **source_game,
                    "task_id": "train-" + hashlib.sha256(
                        source_game["relative_gamefile"].encode("utf-8")
                    ).hexdigest()[:16],
                    "task_type": "pick_and_place_simple",
                }],
            }), encoding="utf-8")
            exclusions = root / "exclusions.json"
            exclusions.write_text(json.dumps({"validation_exclusions": [{
                "relative_gamefile": "task-smoke/trial_1/game.tw-pddl",
                "reason": "prior smoke",
            }]}), encoding="utf-8")

            manifest = build_manifest(data_root, source_manifest, exclusions)
            roles = manifest["roles"]
            self.assertEqual(manifest["inputs"]["source_manifest_sha256"],
                             hashlib.sha256(source_manifest.read_bytes()).hexdigest())
            self.assertEqual(roles["source"]["count"], 1)
            self.assertEqual(roles["target_train"]["count"], 1)
            self.assertEqual(roles["validation"]["count"], 1)
            self.assertEqual(roles["test"]["count"], 1)
            self.assertEqual(roles["target_train"]["games"][0]["template_group"], "task-target")
            self.assertEqual(roles["validation"]["games"][0]["template_group"], "task-validation")
            self.assertEqual(len(roles["validation"]["excluded_smoke_games"]), 1)
            self.assertFalse(roles["validation"]["games"][0]["template_group"] == "task-smoke")

    def test_manifest_rejects_a_changed_eligible_train_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            source_game = make_game(data_root, "train", "task-source", "trial_1")
            make_game(data_root, "valid_seen", "task-validation", "trial_1")
            make_game(data_root, "valid_unseen", "task-test", "trial_1")
            source_manifest = root / "source.json"
            source_manifest.write_text(json.dumps({
                "eligible_pool_size": 2,
                "count": 1,
                "games": [{
                    **source_game,
                    "task_id": "train-source",
                    "task_type": "pick_and_place_simple",
                }],
            }), encoding="utf-8")
            exclusions = root / "exclusions.json"
            exclusions.write_text(json.dumps({"validation_exclusions": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "train inventory differs"):
                build_manifest(data_root, source_manifest, exclusions)

    def test_manifest_write_refuses_to_replace_frozen_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            write_manifest(path, {"version": 1})
            with self.assertRaises(FileExistsError):
                write_manifest(path, {"version": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 1})


if __name__ == "__main__":
    unittest.main()
