# ALFWorld memory assembly

This document records the M4 ALFWorld implementation. WebShop is outside this task.

## Goal

Assemble the source-side memory tuple

`m = (p_minus, a_minus, f, a_tilde, P, N, E, q)`

from the saved M1 failure trajectory, the M2 candidate, and the M3 repair branches. Preserve a reproducible evidence chain and export a machine-readable memory bank.

## Task card

**Goal**: Build one traceable ALFWorld memory from the existing source, candidate, and K=2 repair evidence.

**Context**: M1 saved the failed/truncated source and reset-prefix protocol. M2 produced an unverified candidate with 1-based `t`, mechanism `f`, correction `a_tilde`, and P/N. M3 ran only the correction branch after restoring `1...t-1`; the baseline was not re-executed.

**Rules**:

- Only ALFWorld is in scope for this card.
- The builder is offline; it does not call the teacher or actor.
- Source, candidate, and repair artifacts are read-only inputs. Existing output directories are not overwritten.
- `E` and `q` are generated from observed records. K successful repair branches may be promoted to source-scoped `verified` while restoration is observational; this does not claim exact restoration or cross-task generalization.
- The source failure remains the baseline. No source action `t` or source failure suffix is executed by M4.
- Raw M1-M3 evidence stays embedded in the exported memory; no failed branch is filtered out.

**Acceptance**:

1. Every memory maps the six semantic fields and links to source ID/hash, candidate ID/hash, checkpoint, restoration reports, branch outcomes, and costs.
2. Stable identity uses source hash, candidate ID/hash, and `t`; duplicate repetitions are rejected.
3. Quality states distinguish `verified`, `repair_partial`, `repair_failed`, `blocked`, `error`, and `unverified`; the `verification_level` records observational versus exact restoration.
4. `eligible.jsonl` is explicit. The current K=2 observational result is source-scoped `verified` and is eligible for this initial source memory bank.
5. Summary counts, repair outcomes, source/candidate IDs, deduplication key, and raw cost fields agree with the exported memory.
6. The implementation has unit coverage and the server test suite passes.

## Field mapping

| Memory field | Source |
| --- | --- |
| `p_minus` | Source provenance plus the observable facts and observation immediately before action `t` |
| `a_minus` | The recorded source action at 1-based `t` |
| `f` | M2 candidate mechanism |
| `a_tilde` | M2 candidate correction action |
| `P`, `N` | M2 predicates, reconstructed as typed predicate objects |
| `E` | Full source/candidate/checkpoint references, all M3 branch records, baseline comparison, restoration levels, gate counts, and costs |
| `q` | Program-generated quality state, verification level, source scope, repetition counts, gate/restoration counts, reasons, and admission decision |

The memory ID is a SHA-256 digest of `source_sha256 + candidate_id + candidate_sha256 + t`. M3 raw costs remain integers: recovery environment steps, correction steps, continuation steps, branch trajectory length, actor calls, and inference tokens. The normalized fields remain diagnostics only.

## Command

Run from `/home/ljj/CAVE`:

~~~bash
~/.venvs/cave-alfworld/bin/python -m cave alfworld-memory-build \
  --trajectories output/alfworld-history-sample-seed42-task02-candidate-r01.jsonl \
  --candidates output/alfworld-candidates-task02-r02.jsonl \
  --repairs output/alfworld-repair-candidates-task02-r01.jsonl \
  --expected-repetitions 2 \
  --output-dir output/alfworld-failure-memory-bank-task02-r02
~~~

The command writes:

- `candidates.jsonl`: the selected, still unverified M2 candidate;
- `memories.jsonl`: the assembled memory with full evidence;
- `eligible.jsonl`: only memories admitted by the current rule;
- `summary.json`: cross-file counts and cost totals.

## Server result

The sample assembled one ALFWorld memory:

- source: `cd47a35b181a40d18ac77cea32746c6f`;
- candidate: `64da795db1c3c0952abab8360c981c11e9173017229f49f66594f8e8365d2fff`;
- memory ID: `9bba2726da1d42a7e67950ec6d2cb4c77492c75767ee1a7324db57bf4a89f6bf`;
- M3 outcomes: 2 successes out of K=2;
- quality: `verified` with `verification_level=observational` and `scope=source-scoped`;
- admission: `eligible=1` for the initial source memory bank because both K=2 branches succeeded, restoration was ready, and P/N passed;
- raw cost totals: recovery 32 environment steps, correction 2, continuation 0, actor calls 0, inference tokens 0.

Output directory: `/home/ljj/CAVE/output/alfworld-failure-memory-bank-task02-r02`. The earlier r01 directory is retained as the pre-rule-change audit output.

This is evidence for one source task, gamefile, actor configuration, reset seed, and checkpoint. It is not a cross-task success claim and does not complete WebShop.
