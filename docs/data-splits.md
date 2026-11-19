# ALFWorld data roles for CAVE-Mem

The directories `train`, `valid_seen`, `valid_unseen`, and `valid_train` are present in the downloaded ALFWorld data. They are not created by ExpeL. The official ALFWorld configuration uses `train`, `valid_seen`, and `valid_unseen`. An ALFWorld maintainer described `valid_train` as a leftover from earlier experiments that is not used by the codebase.

Sources: [official ALFWorld configuration](https://github.com/alfworld/alfworld/blob/master/alfworld/agents/README.md), [maintainer explanation of valid_train](https://github.com/alfworld/alfworld/issues/92).

ExpeL has a separate 134-game task list drawn from `valid_unseen`, then constructs two folds within that list for insight extraction and evaluation. These folds are an ExpeL experiment choice, not the four on-disk ALFWorld directories. Sources: [ExpeL task list](https://github.com/LeapLabTHU/ExpeL/blob/main/data/alfworld/alfworld_tasks_suffix.json), [ExpeL benchmark config](https://github.com/LeapLabTHU/ExpeL/blob/main/configs/benchmark/alfworld.yaml), [ExpeL fold code](https://github.com/LeapLabTHU/ExpeL/blob/main/utils.py).

## CAVE-Mem study roles

| CAVE role | ALFWorld source | Assignment |
| --- | --- | --- |
| Historical source runs for memory m | Subset of official `train` | Existing ten-game seed-42 sample; fixed as source |
| Target training pool | Remaining eligible official `train` games | Exclude every source parent template group |
| Validation | `valid_seen` | Exclude the parent template used for the earlier M1 connectivity smoke |
| Final test | `valid_unseen` | Keep reserved and untouched by CAVE design/tuning |

The source sample was selected before outcome inspection, so its role remains fixed. However, the full four-role manifest was not recorded before M1-M4; do not describe it as preregistered before source-memory construction. The target, validation and test role rules are now fixed before target intervention outcomes. Generate the concrete, hashed per-game manifest with `python -m cave.alfworld_data_splits`; the tool refuses to overwrite an existing manifest and fails if the server's train inventory or frozen source files differ.

Source and target-train are split by parent game/template directory, not by broad task type; the same task types can occur in both roles. The previous valid_seen smoke template is excluded from formal validation. `valid_train` is not used. The generator records per-role gamefile hashes and split inventory hashes without reading run outcomes. The final per-game manifest still needs to be generated where the ALFWorld dataset is mounted.

## Checkpoint restoration and target design

ALFWorld supports reset of a selected game and replay of a recorded action prefix. The current adapter declares `observational` restoration and returns no complete state token. The real server M1 checks aligned observations and visible facts for t=1/t=2, but do not certify hidden simulator state equality. The server config has a fixed gamefile/seed and `domain_randomization: false`, so deterministic reset-prefix reconstruction may be implementable. TextWorld 1.7.0 has a `TextWorldEnv.copy()` method for its underlying game state, and a one-game Gym batch uses the synchronous batch implementation even when its registration requests asynchronous mode. The pinned ALFWorld wrapper (commit `aaba6870f86c5be6a08a491f32a50b906227bc3e`) does not expose a complete clone through CAVE. Exact support would need to account for underlying simulator state, RNG, ALFWorld wrapper state and step budgets, then pass independent branch and continuation comparisons. Until that probe and backend pass, target collection defaults to randomized independent Silent/Remind arms with recorded assignment probabilities. See [training-data task cards](training-data-tasks.md).

## Ten-game M1 historical smoke sample

The server had 3,553 runnable ALFWorld `train` games after filtering for the six supported task types, a present `traj_data.json`, `solvable=true`, a gamefile, and a task description. `scripts/prepare_alfworld_history_sample.py` sorts the eligible gamefile paths and samples ten without replacement using Python `random.Random(42)`. It does not select by success or failure outcome. The frozen paths, task IDs, task types and gamefile hashes are in `configs/alfworld-history-sample-seed42/manifest.json`; the corresponding task configs have a 20-step limit.

The task text now comes from each gamefile grammar and matches the goal shown by the simulator. The earlier human-annotation task configs are retained under `configs/alfworld-history-sample-seed42-human-annotation/` as an audit trail; they should not be used for formal memory construction.

The M1 command is `bash scripts/run_alfworld_history_sample.sh`. It runs one attempt per selected game using `gpt-6-sol` and streams Thought/Action/Observation to the terminal. It writes one JSONL trajectory per game. This is a collection smoke run, not a final estimate of performance.
