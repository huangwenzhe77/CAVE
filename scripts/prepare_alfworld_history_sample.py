"""Freeze ten distinct ALFWorld train games for the historical source trajectory sample."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = {
    "pick_and_place_simple", "look_at_obj_in_light",
    "pick_clean_then_place_in_recep", "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep", "pick_two_obj_and_place",
}


def save_fixed(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"Existing selection differs: {path}")
        return
    path.write_text(encoded, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path.home() / "alfworld-data")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    if args.count < 1:
        raise ValueError("count must be positive")
    data_root = args.data_root.resolve()
    split_root = data_root / "json_2.1.1" / "train"
    if not split_root.is_dir():
        raise FileNotFoundError(split_root)
    candidates = []
    for game in sorted(split_root.rglob("game.tw-pddl")):
        relative = game.relative_to(split_root)
        if "movable" in str(relative) or "Sliced" in str(relative):
            continue
        trajectory_meta = game.with_name("traj_data.json")
        if not trajectory_meta.is_file():
            continue
        game_meta = json.loads(game.read_text(encoding="utf-8"))
        if not game_meta.get("solvable", False):
            continue
        task_meta = json.loads(trajectory_meta.read_text(encoding="utf-8"))
        task_type = task_meta.get("task_type")
        if task_type not in SUPPORTED:
            continue
        annotations = task_meta.get("turk_annotations", {}).get("anns", [])
        description = next((a.get("task_desc", "").strip() for a in annotations if a.get("task_desc", "").strip()), "")
        if not description:
            continue
        candidates.append((game, str(relative), task_type, description))
    if len(candidates) < args.count:
        raise ValueError(f"Only {len(candidates)} eligible games")
    chosen = random.Random(args.seed).sample(candidates, args.count)
    output_dir = ROOT / "configs" / f"alfworld-history-sample-seed{args.seed}"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, (game, relative, task_type, human_description) in enumerate(chosen, 1):
        grammar = json.loads(game.read_text(encoding="utf-8"))["grammar"]
        task_section = grammar.split("\"task\": [", 1)[1]
        rhs = task_section.split("\"rhs\":", 1)[1].lstrip()
        simulator_goal, _ = json.JSONDecoder().raw_decode(rhs)
        if not simulator_goal.startswith("Your task is to:"):
            raise ValueError(f"Missing canonical goal in {game}")
        description = simulator_goal.partition(":")[2].strip()
        task_id = "train-" + sha256(relative.encode("utf-8")).hexdigest()[:16]
        filename = f"task-{number:02d}.json"
        task = {
            "benchmark": "alfworld",
            "task_id": task_id,
            "task": description,
            "constraints": ["Complete the specified ALFWorld game using environment actions."],
            "environment": {
                "config_path": str(ROOT / "configs" / "alfworld.server.yaml"),
                "resource_root": str(data_root),
                "gamefile": str(game),
                "split": "train",
                "max_steps": 20,
            },
        }
        save_fixed(output_dir / filename, task)
        rows.append({
            "order": number,
            "task_file": filename,
            "task_id": task_id,
            "task_type": task_type,
            "relative_gamefile": relative,
            "gamefile_sha256": sha256(game.read_bytes()).hexdigest(),
        })
    manifest = {
        "purpose": "Historical source trajectory sample; not target training, validation or test",
        "official_split": "train",
        "data_root": str(data_root),
        "selection": "uniform sample without replacement from sorted eligible gamefiles",
        "task_text_source": "game.tw-pddl grammar task; matches the simulator goal",
        "previous_human_annotation_batch": f"alfworld-history-sample-seed{args.seed}-human-annotation",
        "seed": args.seed,
        "eligible_pool_size": len(candidates),
        "count": args.count,
        "games": rows,
    }
    save_fixed(output_dir / "manifest.json", manifest)
    print(json.dumps({"manifest": str(output_dir / "manifest.json"), "eligible_pool_size": len(candidates),
                      "count": len(rows), "task_types": [row["task_type"] for row in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
