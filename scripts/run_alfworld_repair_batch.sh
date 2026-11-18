#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
trajectories="${1:?Usage: $0 TRAJECTORIES CANDIDATES OUTPUT_DIR}"
candidates="${2:?Usage: $0 TRAJECTORIES CANDIDATES OUTPUT_DIR}"
output_dir="${3:?Usage: $0 TRAJECTORIES CANDIDATES OUTPUT_DIR}"
if [[ -e "$output_dir" && -n "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "Output directory is not empty: $output_dir" >&2
  exit 2
fi
mkdir -p "$output_dir"
set -a
source "$HOME/.config/cave-mem/teacher.env"
set +a
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY before repair verification}"

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
  output="$output_dir/candidate-$number.jsonl"
  echo "########################################"
  echo "CANDIDATE $((i + 1))/${#candidate_ids[@]}: ${candidate_ids[$i]}"
  if [[ -e "$output" || -e "$output.summary.json" ]]; then
    echo "Output already exists: $output" >&2
    exit 2
  fi
  if ! "$HOME/.venvs/cave-alfworld/bin/python" -m cave alfworld-repair-verify       --trajectories "$trajectories"       --candidates "$candidates"       --candidate-id "${candidate_ids[$i]}"       --llm-config configs/llm.server.json       --repetitions 2       --token-budget 20000       --output "$output"; then
    echo "Repair command returned nonzero for candidate ${candidate_ids[$i]}; preserving its output and continuing." >&2
  fi
done

echo "Completed ${#candidate_ids[@]} candidates. Repair records: $output_dir"
