from __future__ import annotations

import json
import random
from pathlib import Path


def generate_trace(path: str | Path, agents: int, seed: int) -> None:
    if agents <= 0:
        raise ValueError("agents must be greater than zero")
    rng = random.Random(seed)
    events: list[dict[str, object]] = []

    for index in range(agents):
        agent_id = f"agent-{index + 1:04d}"
        timestamp = rng.uniform(0, 60)
        events.append(
            {
                "v": 1,
                "ts": round(timestamp, 3),
                "agent_id": agent_id,
                "kind": "agent_started",
                "cpu_cores": rng.choice([0.5, 1.0, 2.0]),
                "memory_gib": rng.choice([1.0, 2.0, 4.0]),
            }
        )

        for cycle in range(rng.randint(2, 5)):
            timestamp += rng.uniform(3, 15)
            wait_type = rng.choices(
                ["model", "tool", "human"], weights=[0.65, 0.25, 0.10], k=1
            )[0]
            events.append(
                {
                    "v": 1,
                    "ts": round(timestamp, 3),
                    "agent_id": agent_id,
                    "kind": f"{wait_type}_wait_started",
                }
            )
            if wait_type == "human":
                timestamp += rng.uniform(120, 900)
            elif wait_type == "tool":
                timestamp += rng.uniform(5, 180)
            else:
                timestamp += rng.uniform(2, 90)
            events.append(
                {
                    "v": 1,
                    "ts": round(timestamp, 3),
                    "agent_id": agent_id,
                    "kind": f"{wait_type}_wait_finished",
                }
            )

        timestamp += rng.uniform(3, 20)
        events.append(
            {
                "v": 1,
                "ts": round(timestamp, 3),
                "agent_id": agent_id,
                "kind": "agent_failed" if rng.random() < 0.05 else "agent_completed",
            }
        )

    events.sort(key=lambda event: (float(event["ts"]), str(event["agent_id"])))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True))
            stream.write("\n")
