from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path

from .schema import Trajectory


def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("Each JSONL row must be an object")
                    yield value
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"{path}:{number}: {exc}") from exc


def write_jsonl(path, rows, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            data = asdict(row) if is_dataclass(row) else row
            stream.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")


def load_trajectories(path):
    result = {}
    for row in read_jsonl(path):
        trajectory = Trajectory.from_dict(row)
        if trajectory.id in result:
            raise ValueError(f"Duplicate trajectory ID: {trajectory.id}")
        result[trajectory.id] = trajectory
    return result


def append_trajectory(path, trajectory):
    """Append one completed attempt without replacing earlier attempts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(trajectory), ensure_ascii=False, allow_nan=False) + "\n"
    lock = path.with_name(path.name + ".lock")
    # Exclusive creation also prevents two collectors appending the same ID concurrently.
    lock_stream = lock.open("x", encoding="utf-8")
    try:
        with lock_stream:
            if path.exists():
                if trajectory.id in load_trajectories(path):
                    raise ValueError(f"Duplicate trajectory ID: {trajectory.id}")
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    needs_newline = stream.tell() > 0
                    if needs_newline:
                        stream.seek(-1, 2)
                        needs_newline = stream.read(1) != b"\n"
            else:
                needs_newline = False
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(("\n" if needs_newline else "") + line)
                stream.flush()
                os.fsync(stream.fileno())
    finally:
        lock.unlink()


def write_json_exclusive(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(row, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)
