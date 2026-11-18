# ALFWorld candidate extraction

This record covers the ALFWorld scope of the candidate extraction task. WebShop is not part of this implementation.

## Goal

Read a saved ALFWorld failure or truncation, propose at most three local hypotheses, and validate the source, action number, evidence quote, correction command, and observable applicability predicates. A candidate remains unverified until a later repair branch executes it.

## Contract

- t is 1-based and points to the suspected action.
- P conditions must evaluate true at the source checkpoint. N conditions must not evaluate true; unknown values remain unknown.
- The correction must be one exact ALFWorld command and must be admissible before t.
- Evidence must quote a recorded observation at or after t, or the recorded termination reason.
- The program copies the source ID, source hash, original action, task ID, outcome, and predicate checks.
- No successful contrast, reflection, repair execution, or memory eligibility is inferred.

## Reproducible commands

~~~
cd /home/ljj/CAVE
set -a
source "$HOME/.config/cave-mem/teacher.env"
set +a
~/.venvs/cave-alfworld/bin/python -m cave alfworld-candidate-prompts --trajectories output/alfworld-history-sample-seed42-task02-candidate-r01.jsonl --output output/alfworld-candidate-prompts-task02.jsonl
~/.venvs/cave-alfworld/bin/python -m cave alfworld-candidate-extract --trajectories output/alfworld-history-sample-seed42-task02-candidate-r01.jsonl --llm-config configs/alfworld-candidate-teacher.server.json --output output/alfworld-candidates-task02-r02.jsonl
~~~

## Real ALFWorld result

Source: cd47a35b181a40d18ac77cea32746c6f, outcome truncated, 18 recorded steps, no successful contrast.

The accepted hypothesis is:

- t=17; original action: put candle 1 in/on countertop 1
- mechanism: the placement command did not match the available command
- correction: move candle 1 to countertop 1
- evidence: step 17 observation contains Nothing happens.
- P checks: both true (move candle 1 to countertop 1 was admissible and the previous action was go to countertop 1)
- status: unverified

A first teacher response was retained as output/alfworld-candidates-task02-r01-precondition-rejected.jsonl; the validator rejected it because one P condition was false at the source checkpoint. It is audit evidence, not an accepted candidate.

## Verification

The full server test suite has 52 passing tests. This task does not execute the correction or claim that it succeeds; that belongs to the repair branch task.
