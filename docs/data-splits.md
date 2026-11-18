# ALFWorld data roles for CAVE-Mem

The directories `train`, `valid_seen`, `valid_unseen`, and `valid_train` are present in the downloaded ALFWorld data. They are not created by ExpeL. The official ALFWorld configuration uses `train`, `valid_seen`, and `valid_unseen`. An ALFWorld maintainer described `valid_train` as a leftover from earlier experiments that is not used by the codebase.

Sources: [official ALFWorld configuration](https://github.com/alfworld/alfworld/blob/master/alfworld/agents/README.md), [maintainer explanation of valid_train](https://github.com/alfworld/alfworld/issues/92).

ExpeL has a separate 134-game task list drawn from `valid_unseen`, then constructs two folds within that list for insight extraction and evaluation. These folds are an ExpeL experiment choice, not the four on-disk ALFWorld directories. Sources: [ExpeL task list](https://github.com/LeapLabTHU/ExpeL/blob/main/data/alfworld/alfworld_tasks_suffix.json), [ExpeL benchmark config](https://github.com/LeapLabTHU/ExpeL/blob/main/configs/benchmark/alfworld.yaml), [ExpeL fold code](https://github.com/LeapLabTHU/ExpeL/blob/main/utils.py).

## Proposed CAVE-Mem roles

| CAVE role | ALFWorld source | Current status |
| --- | --- | --- |
| Historical source runs for memory m | Disjoint subset of official `train` | Ten M1 smoke games frozen below |
| Target training checkpoints for intervention data | Another disjoint subset of official `train` | Formal partition not yet fixed |
| Validation | `valid_seen` | Reserved; one earlier M1 connectivity smoke was run here and is excluded from formal validation |
| Final test | `valid_unseen` | Reserved and untouched by CAVE-Mem source collection |

A formal study manifest must be frozen before outcome inspection. Historical and target-training games should not share a gamefile; for stronger transfer tests, split by the parent task/scene template directory as well. Choose the history/target-training allocation before building the policy dataset. The current ten-game smoke manifest is not a complete four-way study split.

## Ten-game M1 historical smoke sample

The server had 3,553 runnable ALFWorld `train` games after filtering for the six supported task types, a present `traj_data.json`, `solvable=true`, a gamefile, and a task description. `scripts/prepare_alfworld_history_sample.py` sorts the eligible gamefile paths and samples ten without replacement using Python `random.Random(42)`. It does not select by success or failure outcome. The frozen paths, task IDs, task types and gamefile hashes are in `configs/alfworld-history-sample-seed42/manifest.json`; the corresponding task configs have a 20-step limit.

The task text now comes from each gamefile grammar and matches the goal shown by the simulator. The earlier human-annotation task configs are retained under `configs/alfworld-history-sample-seed42-human-annotation/` as an audit trail; they should not be used for formal memory construction.

The M1 command is `bash scripts/run_alfworld_history_sample.sh`. It runs one attempt per selected game using `gpt-6-sol` and streams Thought/Action/Observation to the terminal. It writes one JSONL trajectory per game. This is a collection smoke run, not a final estimate of performance.
