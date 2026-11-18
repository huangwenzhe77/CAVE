#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
run_id="${1:-alfworld-history-sample-seed42-r02}"
if [[ ! "$run_id" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "Run ID must contain only letters, digits, underscore or hyphen" >&2
  exit 2
fi
output="output/${run_id}.jsonl"
if [[ -e "$output" ]]; then
  echo "Output already exists: $output. Choose another run ID to preserve earlier attempts." >&2
  exit 2
fi
mapfile -t tasks < <(find configs/alfworld-history-sample-seed42 -maxdepth 1 -type f -name "task-*.json" | sort)
if [[ "${#tasks[@]}" -ne 10 ]]; then
  echo "Expected exactly 10 frozen task configs; found ${#tasks[@]}" >&2
  exit 2
fi
set -a
source "$HOME/.config/cave-mem/teacher.env"
set +a
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY before collection}"
mkdir -p output
for i in "${!tasks[@]}"; do
  echo "########################################"
  echo "TASK $((i + 1))/10: ${tasks[$i]}"
  "$HOME/.venvs/cave-alfworld/bin/python" -m cave collect \
    --task "${tasks[$i]}" \
    --llm-config configs/llm.server.json \
    --run-id "$run_id" \
    --attempts 1 \
    --seed 42 \
    --output "$output" \
    --trace
done
echo "Completed 10 tasks. Trajectories: $output"
