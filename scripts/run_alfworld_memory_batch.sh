#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
trajectories="${1:?Usage: $0 TRAJECTORIES CANDIDATES REPAIR_DIR OUTPUT_DIR}"
candidates="${2:?Usage: $0 TRAJECTORIES CANDIDATES REPAIR_DIR OUTPUT_DIR}"
repair_dir="${3:?Usage: $0 TRAJECTORIES CANDIDATES REPAIR_DIR OUTPUT_DIR}"
output_dir="${4:?Usage: $0 TRAJECTORIES CANDIDATES REPAIR_DIR OUTPUT_DIR}"
if [[ -e "$output_dir" && -n "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "Output directory is not empty: $output_dir" >&2
  exit 2
fi
mkdir -p "$output_dir"
mapfile -t candidate_ids < <("$HOME/.venvs/cave-alfworld/bin/python" - "$candidates" <<'PY'
import json
import sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.strip():
        print(json.loads(line)["id"])
PY
)
if [[ "${#candidate_ids[@]}" -eq 0 ]]; then
  echo "No candidates found" >&2
  exit 2
fi

for i in "${!candidate_ids[@]}"; do
  number=$(printf "%02d" "$((i + 1))")
  repairs="$repair_dir/candidate-$number.jsonl"
  memory_dir="$output_dir/candidate-$number"
  if [[ ! -f "$repairs" ]]; then
    echo "Missing repair file: $repairs" >&2
    exit 2
  fi
  echo "########################################"
  echo "MEMORY $((i + 1))/${#candidate_ids[@]}: ${candidate_ids[$i]}"
  "$HOME/.venvs/cave-alfworld/bin/python" -m cave alfworld-memory-build     --trajectories "$trajectories"     --candidates "$candidates"     --repairs "$repairs"     --candidate-id "${candidate_ids[$i]}"     --expected-repetitions 2     --output-dir "$memory_dir"
done

"$HOME/.venvs/cave-alfworld/bin/python" - "$output_dir" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1])
rows = []
for path in sorted(root.glob("candidate-*/summary.json")):
    row = json.loads(path.read_text(encoding="utf-8"))
    row["memory_dir"] = str(path.parent.resolve())
    rows.append(row)
summary = {
    "protocol": "alfworld-memory-batch-summary-v1",
    "benchmark": "alfworld",
    "candidates": len(rows),
    "memories": sum(row["memories"] for row in rows),
    "eligible": sum(row["eligible"] for row in rows),
    "statuses": dict(Counter(next(iter(row["statuses"])) for row in rows)),
    "repair_outcomes": dict(Counter(
        outcome
        for row in rows
        for outcome, count in row["repair_outcomes"].items()
        for _ in range(count)
    )),
    "source_ids": [source for row in rows for source in row["source_ids"]],
    "candidate_ids": [candidate for row in rows for candidate in row["candidate_ids"]],
    "memory_ids": [memory for row in rows for memory in row["memory_ids"]],
    "per_memory": rows,
}
(root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "candidates": summary["candidates"],
    "memories": summary["memories"],
    "eligible": summary["eligible"],
    "statuses": summary["statuses"],
    "repair_outcomes": summary["repair_outcomes"],
    "output_dir": str(root.resolve()),
}, ensure_ascii=False, indent=2))
PY
