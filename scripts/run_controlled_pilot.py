#!/usr/bin/env python3
"""Run a small, explicitly non-production recorder and analyzer exercise."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent_fleet_controller.analysis import analyze_paths
from agent_fleet_controller.recorder import record_command
from agent_fleet_controller.simulation import SimulationPolicy


WORKLOADS = {
    "coding-agent": {
        "cpu_cores": 2.0,
        "memory_gib": 4.0,
        "source": """
from agent_fleet_controller.markers import wait
import time

sum(value * value for value in range(200_000))
with wait("model"):
    time.sleep(0.12)
sum(value * value for value in range(100_000))
with wait("tool"):
    time.sleep(0.08)
""",
    },
    "research-agent": {
        "cpu_cores": 1.0,
        "memory_gib": 2.0,
        "source": """
from agent_fleet_controller.markers import wait
import time

with wait("model"):
    time.sleep(0.25)
with wait("tool"):
    time.sleep(0.15)
""",
    },
}


def _write_private_json(path: Path, payload: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs-per-class", type=int, default=30)
    args = parser.parse_args()

    if args.runs_per_class <= 0:
        parser.error("--runs-per-class must be greater than zero")
    if args.output.exists():
        parser.error(f"refusing to reuse existing output directory: {args.output}")

    args.output.mkdir(parents=True, mode=0o700)
    for workload_class, configuration in WORKLOADS.items():
        class_directory = args.output / workload_class
        class_directory.mkdir(mode=0o700)
        for run_number in range(1, args.runs_per_class + 1):
            trace_path = class_directory / f"run-{run_number:03d}.jsonl"
            record_command(
                [sys.executable, "-c", str(configuration["source"])],
                trace_path=trace_path,
                metrics_path=trace_path.with_suffix(".metrics.jsonl"),
                cpu_cores=float(configuration["cpu_cores"]),
                memory_gib=float(configuration["memory_gib"]),
                workload_class=workload_class,
                sample_interval=0.1,
            )
            print(
                f"recorded {workload_class} {run_number}/{args.runs_per_class}",
                file=sys.stderr,
            )

    policy = SimulationPolicy(
        idle_after_seconds=0.05,
        checkpoint_seconds=0.005,
        resume_seconds=0.01,
        cpu_hourly_cost=0.05,
        memory_gib_hourly_cost=0.006,
    )
    report = analyze_paths(
        [args.output],
        policy,
        minimum_traces_per_class=args.runs_per_class,
        minimum_workload_classes=2,
    )
    report["benchmark"] = {
        "kind": "controlled_local_functional_pilot",
        "real_agent_workload": False,
        "external_sandbox_runtime": False,
        "warning": "Functional evidence only; do not present as production savings.",
    }
    report_path = args.output / "report.json"
    _write_private_json(report_path, report)
    print(json.dumps({"report": str(report_path), "status": "complete"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
