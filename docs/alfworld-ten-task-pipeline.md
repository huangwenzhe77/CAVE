# ALFWorld ten-task pipeline run

This report covers one initial ALFWorld run on the fixed ten-task train sample. WebShop is outside this run.

## Artifacts

- Collection: `output/alfworld-history-sample-seed42-r02.jsonl`
- Collection summary: `output/alfworld-history-sample-seed42-r02.summary.json`
- M2 candidates: `output/alfworld-candidates-history-sample-seed42-r03.jsonl`
- M3 repair branches: `output/alfworld-repair-history-sample-seed42-r03/`
- M4 memories: `output/alfworld-memory-history-sample-seed42-r03/`
- Combined summary: `output/alfworld-pipeline-history-sample-seed42-r03.summary.json`

All ten tasks use the frozen configs in `configs/alfworld-history-sample-seed42/`, the train split, a 20-step environment limit, and `gpt-6-sol`.

## Collection

The actor completed 1 of 10 tasks and truncated 9:

| Result | Count |
| --- | ---: |
| success | 1 |
| truncated | 9 |
| error | 0 |

Eight truncations ended at the ALFWorld adapter's repeated-action guard and one reached the 20-step budget. The adapter normalizes the placement forms `in`, `on`, and `in/on` before this guard, so different raw strings can still be the same guarded action. There were 31 `Nothing happens.` observations, 105 environment steps, and 433,389 actor inference tokens.

## Candidate extraction

The nine failed/truncated sources produced ten validated candidates. Five candidates point to the action immediately before the final recorded action, and five point earlier in the trajectory. One source produced two candidates.

The teacher proposed the common placement correction `move object to target` for most candidates. The candidate validator checked the original action, source hash, admissible command, evidence quote, and P/N predicates. The teacher response parser also preserves the raw audit response while tolerating the observed invalid JSON apostrophe escape `\\'`.

## Repair verification

Each candidate received K=2 correction branches. The source baseline was read but not re-executed.

| Branch outcome | Count |
| --- | ---: |
| success | 10 |
| failure | 6 |
| blocked | 4 |
| error | 0 |

The ten successful branches correspond to five candidates. The six failures exhausted inference or hit the repeated-action guard during continuation. The four blocked branches failed the P/N gate before executing the correction. Repair verification used 140 prefix recovery environment steps, 38 branch environment steps, 25 continuation actor calls, and 97,549 actor inference tokens.

## Memory assembly

M4 assembled all ten candidates, retaining successful, failed, and blocked evidence:

| Memory status | Count | Eligible |
| --- | ---: | ---: |
| verified | 5 | 5 |
| repair_failed | 3 | 0 |
| blocked | 2 | 0 |

For this initial ALFWorld source bank, `verified` means K=2 branches succeeded, restoration was ready, and P/N passed. The records still carry `verification_level=observational` and `scope=source-scoped`; they are not exact-restoration or cross-task claims.

Compared with the initial 1/10 actor success, the run produced five additional source-specific successful correction branches. This suggests six observed success paths out of ten source tasks if the eligible memories are applied, but this is not a paired benchmark success estimate.
