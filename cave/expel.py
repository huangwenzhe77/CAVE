"""Import explicit ExpeL trial exports without importing ExpeL or unpickling checkpoints."""

import re

from .schema import Step, Trajectory


def parse_trial(data: dict) -> Trajectory:
    benchmark = data["benchmark"]
    if benchmark not in {"alfworld", "webshop"}:
        raise ValueError("Expected alfworld or webshop")
    raw = data["trajectory"]
    steps, thoughts, observation, action, initial = [], [], [], None, []

    def flush():
        nonlocal action, thoughts, observation
        if action is not None:
            steps.append(Step(action, "\n".join(observation).strip(), tuple(thoughts)))
            thoughts, observation, action = [], [], None

    # ALFWorld thoughts are not env actions in ExpeL; WebShop think[] is an env
    # step and consumes budget, so it must remain in the recorded action prefix.
    for line in raw.splitlines():
        stripped = line.strip()
        if benchmark == "alfworld":
            if stripped.lower().startswith("> think:"):
                flush()
                thoughts.append(stripped[len("> think:"):].strip())
            elif stripped.startswith(">"):
                flush()
                action = stripped[1:].strip()
                if not action:
                    raise ValueError("Empty ALFWorld action")
            elif action is None:
                if thoughts:
                    thoughts.append(line)
                else:
                    initial.append(line)
            else:
                observation.append(line)
        else:
            match = re.match(r"^Action(?:\s+\d+)?:\s*(.*)$", stripped, re.I)
            if match:
                flush()
                action = match.group(1)
                if action.lower().startswith("think["):
                    thoughts.append(action[6:-1])
            else:
                line = re.sub(r"^Observation(?:\s+\d+)?:\s*", "", line, flags=re.I)
                (observation if action is not None else initial).append(line)
    flush()
    metadata = {k: v for k, v in data.items() if k != "trajectory"}
    metadata.setdefault("initial_observation", "\n".join(initial).strip())
    return Trajectory.from_dict({**metadata, "steps": [s.__dict__ for s in steps], "raw_text": raw})
