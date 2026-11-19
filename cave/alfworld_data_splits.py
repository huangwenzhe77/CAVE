"""Build a frozen, outcome-independent ALFWorld study split manifest."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path


DATA_LAYOUT = "json_2.1.1"
SUPPORTED_TASK_TYPES = {
    "pick_and_place_simple",
    "look_at_obj_in_light",
    "pick_clean_then_place_in_recep",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
    "pick_two_obj_and_place",
}


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _eligible_games(data_root: Path, split: str) -> list[dict]:
    split_root = data_root / DATA_LAYOUT / split
    if not split_root.is_dir():
        raise FileNotFoundError(split_root)

    games = []
    for gamefile in sorted(split_root.rglob("game.tw-pddl")):
        relative = gamefile.relative_to(split_root).as_posix()
        if "movable" in relative or "Sliced" in relative:
            continue
        trajectory_file = gamefile.with_name("traj_data.json")
        if not trajectory_file.is_file():
            continue

        game_meta = json.loads(gamefile.read_text(encoding="utf-8"))
        trajectory_meta = json.loads(trajectory_file.read_text(encoding="utf-8"))
        task_type = trajectory_meta.get("task_type")
        if not game_meta.get("solvable", False) or task_type not in SUPPORTED_TASK_TYPES:
            continue
        annotations = trajectory_meta.get("turk_annotations", {}).get("anns", [])
        if not any(item.get("task_desc", "").strip() for item in annotations):
            continue

        games.append({
            "task_id": f"{split}-{sha256(relative.encode('utf-8')).hexdigest()[:16]}",
            "official_split": split,
            "relative_gamefile": relative,
            "template_group": Path(relative).parts[0],
            "task_type": task_type,
            "gamefile_sha256": file_sha256(gamefile),
            "trajectory_metadata_sha256": file_sha256(trajectory_file),
        })
    return games


def _inventory_hash(games: list[dict]) -> str:
    rows = [
        f"{row['official_split']}\t{row['relative_gamefile']}\t"
        f"{row['gamefile_sha256']}\t{row['trajectory_metadata_sha256']}"
        for row in games
    ]
    return sha256("\n".join(rows).encode("utf-8")).hexdigest()


def build_manifest(data_root: Path, source_manifest_path: Path,
                   exclusion_path: Path) -> dict:
    data_root = data_root.resolve()
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    exclusions = json.loads(exclusion_path.read_text(encoding="utf-8"))

    train = _eligible_games(data_root, "train")
    if source_manifest.get("eligible_pool_size") != len(train):
        raise ValueError(
            "ALFWorld train inventory differs from the frozen source sample "
            f"({len(train)} != {source_manifest.get('eligible_pool_size')})"
        )
    train_by_path = {row["relative_gamefile"]: row for row in train}
    source_rows = source_manifest.get("games", [])
    if source_manifest.get("count") != len(source_rows):
        raise ValueError("Frozen source manifest count does not match its game list")
    source_paths = [row["relative_gamefile"] for row in source_rows]
    if len(source_paths) != len(set(source_paths)):
        raise ValueError("Source manifest contains duplicate gamefiles")

    source = []
    for frozen in source_rows:
        current = train_by_path.get(frozen["relative_gamefile"])
        if current is None:
            raise ValueError(f"Frozen source game is missing or ineligible: {frozen['relative_gamefile']}")
        if current["gamefile_sha256"] != frozen["gamefile_sha256"]:
            raise ValueError(f"Frozen source game changed: {frozen['relative_gamefile']}")
        if current["task_id"] != frozen["task_id"] or current["task_type"] != frozen["task_type"]:
            raise ValueError(f"Frozen source metadata changed: {frozen['relative_gamefile']}")
        source.append(current)

    source_groups = {row["template_group"] for row in source}
    source_path_set = set(source_paths)
    target_train = [row for row in train
                    if row["relative_gamefile"] not in source_path_set
                    and row["template_group"] not in source_groups]

    validation_all = _eligible_games(data_root, "valid_seen")
    test = _eligible_games(data_root, "valid_unseen")
    validation_by_path = {row["relative_gamefile"]: row for row in validation_all}
    validation_exclusions = exclusions.get("validation_exclusions", [])
    excluded_groups = set()
    excluded_rows = []
    for exclusion in validation_exclusions:
        relative = exclusion["relative_gamefile"]
        row = validation_by_path.get(relative)
        if row is None:
            raise ValueError(f"Declared validation smoke task is missing or ineligible: {relative}")
        excluded_groups.add(row["template_group"])
        excluded_rows.append({
            "task_id": row["task_id"],
            "relative_gamefile": relative,
            "template_group": row["template_group"],
            "reason": exclusion["reason"],
        })
    validation = [row for row in validation_all if row["template_group"] not in excluded_groups]

    if set(source_paths) & {row["relative_gamefile"] for row in target_train}:
        raise ValueError("Source and target-train gamefiles overlap")
    if source_groups & {row["template_group"] for row in target_train}:
        raise ValueError("Source and target-train template groups overlap")
    if {row["relative_gamefile"] for row in validation} & {
        row["relative_gamefile"] for row in excluded_rows
    }:
        raise ValueError("A previous validation smoke game remains in validation")

    return {
        "schema_version": 1,
        "purpose": "Frozen ALFWorld source/target/validation/test roles for CAVE target-data construction",
        "inputs": {
            "data_root": str(data_root),
            "source_manifest_sha256": file_sha256(source_manifest_path),
            "validation_exclusions_sha256": file_sha256(exclusion_path),
        },
        "dataset": {
            "layout": DATA_LAYOUT,
            "eligible_filter": {
                "supported_task_types": sorted(SUPPORTED_TASK_TYPES),
                "requires_solvable": True,
                "requires_gamefile_and_traj_data": True,
                "requires_nonempty_task_description": True,
                "excludes_path_fragments": ["movable", "Sliced"],
            },
            "train_inventory_count": len(train),
            "train_inventory_sha256": _inventory_hash(train),
            "valid_seen_inventory_count": len(validation_all),
            "valid_seen_inventory_sha256": _inventory_hash(validation_all),
            "valid_unseen_inventory_count": len(test),
            "valid_unseen_inventory_sha256": _inventory_hash(test),
            "valid_train_used": False,
        },
        "roles": {
            "source": {
                "official_split": "train",
                "selection": "existing seed-42 historical source sample, selected before outcome inspection",
                "count": len(source),
                "games": source,
            },
            "target_train": {
                "official_split": "train",
                "selection": "all eligible games outside frozen source template groups; no outcome-based selection",
                "count": len(target_train),
                "games": target_train,
            },
            "validation": {
                "official_split": "valid_seen",
                "selection": "all eligible games except prior CAVE smoke template groups",
                "excluded_smoke_games": excluded_rows,
                "count": len(validation),
                "games": validation,
            },
            "test": {
                "official_split": "valid_unseen",
                "selection": "all eligible games; reserved and not used for design or tuning",
                "count": len(test),
                "games": test,
            },
        },
        "checkpoint_restore_audit": {
            "adapter": "CAVE AlfworldEnvironment",
            "declared_restoration": "observational",
            "complete_state_token_available": False,
            "evidence": [
                "cave/benchmarks.py: AlfworldEnvironment.restoration is observational",
                "cave/benchmarks.py: AlfworldEnvironment.state_token returns None",
                "docs/server-collection-acceptance.md: real reset-prefix checks passed at observational level",
            ],
            "paired_counterfactual_supported": False,
            "target_collection_default": "randomized_independent_arms",
            "upgrade_condition": "A backend must expose and compare a complete simulator state token across independent restores.",
        },
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True,
                        help="ALFWorld data root containing json_2.1.1/")
    parser.add_argument("--source-manifest", type=Path,
                        default=root / "configs/alfworld-history-sample-seed42/manifest.json")
    parser.add_argument("--exclusions", type=Path,
                        default=root / "configs/alfworld-study/validation-exclusions.json")
    parser.add_argument("--output", type=Path,
                        default=root / "configs/alfworld-study/splits.json")
    args = parser.parse_args(argv)
    manifest = build_manifest(args.data_root, args.source_manifest, args.exclusions)
    write_manifest(args.output, manifest)
    counts = {name: role["count"] for name, role in manifest["roles"].items()}
    print(json.dumps({"manifest": str(args.output), "counts": counts,
                      "restoration": manifest["checkpoint_restore_audit"]["declared_restoration"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
