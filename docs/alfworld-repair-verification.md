# ALFWorld repair verification

This record covers the ALFWorld scope of repair verification. WebShop is outside this task.

## Goal and protocol

Use an existing failed trajectory as the baseline. For each repetition, reset the same game, replay only actions 1 through t-1, check restoration and P/N, execute the candidate correction, and let the recorded actor continue until success, failure, or the remaining environment budget is exhausted.

The baseline is not re-executed. K is set to 2 repeated repair branches for the initial experiment. The two branches use the same source reset seed and distinct continuation actor seed offsets. This is not a paired baseline experiment.

## Cost definitions

ExpeL exposes an integer trajectory step count and counts trajectory text in tokens. CAVE records the same kinds of values separately:

- trajectory_length: full branch history, prefix plus correction and any continuation.
- branch_trajectory_length: actions after the restored checkpoint.
- recovery_environment_steps: actions replayed from the source prefix; these do not call the actor.
- correction_environment_steps: one when the correction executes.
- continuation_environment_steps: environment actions generated after the correction.
- inference_tokens: API-reported actor usage for the repair branch.
- normalized values are budget ratios only; they are not monetary cost.

With a 20-step source limit and t=17, recovery consumes 16 steps, the repair checkpoint has 4 remaining steps, and the correction consumes 1 of those 4. At most 3 actor continuation actions remain.

## Command

~~~
cd /home/ljj/CAVE
set -a
source "$HOME/.config/cave-mem/teacher.env"
set +a
~/.venvs/cave-alfworld/bin/python -m cave alfworld-repair-verify --trajectories output/alfworld-history-sample-seed42-task02-candidate-r01.jsonl --candidates output/alfworld-candidates-task02-r02.jsonl --llm-config configs/llm.server.json --repetitions 2 --token-budget 20000 --output output/alfworld-repair-candidates-task02-r01.jsonl
~~~

## Real result

Source trajectory: cd47a35b181a40d18ac77cea32746c6f. The baseline is truncated after 18 steps with termination reason repeated_action. The candidate selects t=17, immediately before the final recorded step.

Both repetitions produced:

- restoration ready: true
- restoration level: observational
- replayed prefix actions: 16
- P results: true, true
- N results: empty
- correction: move candle 1 to countertop 1
- repair outcome: success
- environment score: 1.0
- correction actions: 1
- continuation actions: 0
- inference tokens: 0
- remaining environment steps at checkpoint: 4
- full trajectory_length: 17
- branch_trajectory_length: 1

The correction immediately produced the successful ALFWorld terminal observation, so no continuation actor call was needed. The source failure was not re-executed. The two branch records are in output/alfworld-repair-candidates-task02-r01.jsonl and the aggregate is in output/alfworld-repair-candidates-task02-r01.jsonl.summary.json.

The result supports this correction for this source task, gamefile, actor configuration, reset seed and observational restoration. It does not establish cross-task generalization or prove the mechanism beyond this source.

## Selection and P/N monitoring

The M2 selection summary is output/alfworld-candidates-task02-r02.jsonl.summary.json. In this one-source sample, the teacher selected t=17 from an 18-step trajectory: 0 last-step selections, 1 immediately-before-last selection, and 0 earlier selections. One example cannot establish a teacher tendency or show that other nodes are systematically ignored. Future candidate batches record the same position counts.

M3 records P/N gate outcomes per repetition and counts blocked branches separately. A P condition that is false or unknown, or an N condition that is true or unknown, blocks the repair branch and is preserved as evidence. This gate count is a verification-entry statistic; it is not yet the final M4 memory admission rule.

## Verification

The implementation saves success, failure, blocked and error branches with source/candidate references, restoration checks, P/N results, terminal oracle, remaining budget, trajectory length and API token usage. The server test suite covers the offline success and blocked paths; no WebShop path is included.
