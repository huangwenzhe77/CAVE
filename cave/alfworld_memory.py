"""Assemble ALFWorld failure memories from M1-M3 JSONL artifacts.

This module is deliberately offline. It copies source, candidate, and repair
evidence into one traceable memory record and never asks a teacher to assign
the quality or admission status.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Iterable

from .schema import Memory, Predicate, digest, text


PROTOCOL = "alfworld-memory-bank-v1"


def _predicates(candidate: dict, name: str) -> tuple[Predicate, ...]:
    rows = candidate.get(name)
    if not isinstance(rows, list):
        raise ValueError(f"candidate.{name} must be a list")
    return tuple(Predicate(**row) for row in rows)


def _source_provenance(source) -> dict:
    return {
        "id": source.id,
        "sha256": source.fingerprint,
        "benchmark": source.benchmark,
        "task_id": source.task_id,
        "task": source.task,
        "run_id": source.run_id,
        "outcome": source.outcome,
        "termination_reason": source.termination_reason,
        "seed": source.seed,
        "attempt": source.attempt,
        "actor": source.actor,
        "actor_config": deepcopy(source.actor_config),
        "environment": deepcopy(source.environment),
        "budget": deepcopy(source.budget),
        "metrics": deepcopy(source.metrics),
        "terminal": deepcopy(source.terminal),
        "trajectory_length": len(source.steps),
    }


def _checkpoint(source, candidate: dict) -> dict:
    t = candidate.get("t")
    if type(t) is not int or not 1 <= t <= len(source.steps):
        raise ValueError("candidate.t must be a recorded 1-based action number")
    step = source.steps[t - 1]
    if candidate.get("step_index") != t - 1:
        raise ValueError("candidate.step_index must equal candidate.t - 1")
    if candidate.get("original_action") != step.action:
        raise ValueError("candidate.original_action does not match source action")
    return {
        "t": t,
        "step_index": t - 1,
        "facts_before": deepcopy(step.facts_before),
        "observation": step.observation,
        "original_action": step.action,
        "inference_tokens": step.inference_tokens,
    }


def _validate_inputs(source, candidate: dict, repair_records: list[dict], expected_repetitions: int):
    if source.benchmark != "alfworld":
        raise ValueError("M4 currently supports ALFWorld only")
    if source.outcome not in {"failure", "truncated"}:
        raise ValueError("M4 source must be a failure or truncated trajectory")
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    if candidate.get("benchmark") != "alfworld":
        raise ValueError("candidate benchmark must be alfworld")
    if candidate.get("source_id") != source.id:
        raise ValueError("candidate source_id does not match source")
    if candidate.get("source_sha256") != source.fingerprint:
        raise ValueError("candidate source_sha256 does not match source")
    text(candidate.get("id"), "candidate.id")
    text(candidate.get("mechanism"), "candidate.mechanism")
    text(candidate.get("correction"), "candidate.correction")
    p = _predicates(candidate, "P")
    n = _predicates(candidate, "N")
    if not p:
        raise ValueError("candidate.P must not be empty")
    checkpoint = _checkpoint(source, candidate)

    candidate_sha256 = digest(candidate)
    seen = set()
    for record in repair_records:
        if not isinstance(record, dict):
            raise ValueError("Every repair record must be an object")
        if record.get("source_id") != source.id or record.get("source_sha256") != source.fingerprint:
            raise ValueError("repair record source does not match selected source")
        if record.get("candidate_id") != candidate["id"]:
            raise ValueError("repair record candidate_id does not match selected candidate")
        if record.get("candidate_sha256") != candidate_sha256:
            raise ValueError("repair record candidate_sha256 does not match selected candidate")
        repetition = record.get("repetition")
        if type(repetition) is not int or repetition < 0:
            raise ValueError("repair record repetition must be a nonnegative integer")
        if repetition in seen:
            raise ValueError(f"Duplicate repair repetition: {repetition}")
        seen.add(repetition)
        if record.get("t") != checkpoint["t"] or record.get("original_action") != checkpoint["original_action"]:
            raise ValueError("repair record checkpoint does not match candidate")
    return p, n, checkpoint, candidate_sha256


def _branch_quality(repair_records: list[dict], expected_repetitions: int) -> tuple[str, list[str]]:
    reasons = []
    if not repair_records:
        return "unverified", ["No M3 repair branch evidence was supplied."]
    if len(repair_records) != expected_repetitions:
        reasons.append(f"Expected {expected_repetitions} repair branches, got {len(repair_records)}.")
    statuses = [record.get("status") for record in repair_records]
    if any(status == "error" for status in statuses):
        reasons.append("At least one repair branch raised an error.")
        return "error", reasons
    if all(status == "blocked" for status in statuses):
        reasons.append("Every repair branch was blocked before or at the correction gate.")
        return "blocked", reasons
    if all(status == "success" for status in statuses):
        for record in repair_records:
            restoration = record.get("restoration") or {}
            if restoration.get("ready") is not True:
                reasons.append(f"Repetition {record.get('repetition')} was not restored ready.")
            if record.get("precondition_status") != "passed":
                reasons.append(f"Repetition {record.get('repetition')} did not pass the P/N gate.")
            if not all(value is True for value in (record.get("P_results") or [])):
                reasons.append(f"Repetition {record.get('repetition')} has a non-true P result.")
            if not all(value is False for value in (record.get("N_results") or [])):
                reasons.append(f"Repetition {record.get('repetition')} has a non-false N result.")
        if not reasons and len(repair_records) == expected_repetitions:
            return "verified", [
                "All K repair branches reached environment success with ready restoration and passed P/N; "
                "verification is source-scoped."
            ]
        return "repair_partial", reasons
    if any(status == "success" for status in statuses):
        reasons.append("Some repair branches succeeded and some did not.")
        return "repair_partial", reasons
    reasons.append("No repair branch reached environment success.")
    return "repair_failed", reasons


def _cost_summary(repair_records: list[dict]) -> dict:
    fields = (
        "recovery_environment_steps",
        "correction_environment_steps",
        "continuation_environment_steps",
        "branch_trajectory_length",
        "actor_calls",
        "inference_tokens",
    )
    totals = {f"total_{field}": sum((record.get("cost") or {}).get(field, 0) or 0
                                    for record in repair_records) for field in fields}
    return {
        **totals,
        "per_repetition": [deepcopy(record.get("cost") or {}) for record in repair_records],
        "source_baseline_reexecuted": False,
        "normalized_fields_are_diagnostics": True,
    }


def build_memory(source, candidate: dict, repair_records: Iterable[dict], expected_repetitions: int = 2) -> Memory:
    if type(expected_repetitions) is not int or expected_repetitions < 1:
        raise ValueError("expected_repetitions must be a positive integer")
    records = [deepcopy(record) for record in repair_records]
    predicates_p, predicates_n, checkpoint, candidate_sha256 = _validate_inputs(
        source, candidate, records, expected_repetitions)
    status, quality_reasons = _branch_quality(records, expected_repetitions)
    branch_statuses = Counter(record.get("status", "missing") for record in records)
    restoration_levels = Counter((record.get("restoration") or {}).get("restoration", "missing")
                                 for record in records)
    p_gate = Counter(record.get("precondition_status", "missing") for record in records)
    memory_id = digest({
        "protocol": PROTOCOL,
        "source_sha256": source.fingerprint,
        "candidate_id": candidate["id"],
        "candidate_sha256": candidate_sha256,
        "t": candidate["t"],
    })
    p_minus = {
        "source": _source_provenance(source),
        "checkpoint": checkpoint,
        "candidate_position": deepcopy((records[0].get("candidate_position") if records else None)),
    }
    evidence = {
        "protocol": PROTOCOL,
        "source": _source_provenance(source),
        "candidate": deepcopy(candidate),
        "checkpoint": checkpoint,
        "repair": {
            "expected_repetitions": expected_repetitions,
            "completed_repetitions": len(records),
            "records": records,
            "statuses": dict(branch_statuses),
            "restoration_levels": dict(restoration_levels),
            "predicate_gate": dict(p_gate),
            "cost": _cost_summary(records),
        },
        "scope": (
            "Evidence is limited to this ALFWorld source task, candidate checkpoint, "
            "environment configuration, actor configuration, and supplied repetitions."
        ),
        "baseline": {
            "outcome": source.outcome,
            "termination_reason": source.termination_reason,
            "trajectory_length": len(source.steps),
            "reexecuted": False,
        },
    }
    verification_level = (
        "observational" if restoration_levels.get("observational")
        else "exact" if restoration_levels.get("exact")
        else "unknown"
    )
    q = {
        "status": status,
        "eligible": status == "verified",
        "verification_level": verification_level,
        "scope": "source-scoped",
        "quality_reasons": quality_reasons,
        "expected_repetitions": expected_repetitions,
        "completed_repetitions": len(records),
        "branch_statuses": dict(branch_statuses),
        "restoration_levels": dict(restoration_levels),
        "predicate_gate": dict(p_gate),
        "admission_rule": (
            "For the initial ALFWorld source bank, exactly K successful repair branches with ready "
            "restoration and passed P/N are verified and eligible. The evidence remains source-scoped; "
            "observational restoration is not an exact or cross-task claim."
        ),
    }
    return Memory(
        id=memory_id,
        p_minus=p_minus,
        a_minus=checkpoint["original_action"],
        f=candidate["mechanism"],
        a_tilde=candidate["correction"],
        P=predicates_p,
        N=predicates_n,
        E=evidence,
        q=q,
    )


def build_memory_bank(source, candidate: dict, repair_records: Iterable[dict], expected_repetitions: int = 2):
    records = [deepcopy(record) for record in repair_records]
    memory = build_memory(source, candidate, records, expected_repetitions)
    status = memory.q["status"]
    summary = {
        "protocol": PROTOCOL,
        "benchmark": "alfworld",
        "expected_repetitions": expected_repetitions,
        "sources": 1,
        "candidates": 1,
        "memories": 1,
        "eligible": int(memory.eligible),
        "statuses": {status: 1},
        "repair_outcomes": dict(Counter(record.get("status", "missing") for record in records)),
        "source_ids": [source.id],
        "candidate_ids": [candidate["id"]],
        "memory_ids": [memory.id],
        "deduplication": {
            "key": "source_sha256 + candidate_id + candidate_sha256 + t",
            "duplicates_collapsed": 0,
        },
        "cost": memory.E["repair"]["cost"],
        "scope": "One ALFWorld source failure memory; no cross-task or WebShop claim.",
    }
    return memory, summary
