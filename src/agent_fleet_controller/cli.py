from __future__ import annotations

import argparse
import json
import sys

from .generator import generate_trace
from .simulation import SimulationPolicy, simulate
from .trace import TraceValidationError, load_trace


def _policy_from_args(args: argparse.Namespace) -> SimulationPolicy:
    return SimulationPolicy(
        idle_after_seconds=args.idle_after,
        checkpoint_seconds=args.checkpoint_seconds,
        resume_seconds=args.resume_seconds,
        cpu_hourly_cost=args.cpu_hourly,
        memory_gib_hourly_cost=args.memory_gib_hourly,
        minimum_resource_savings_percent=args.minimum_savings,
        maximum_completion_overhead_percent=args.maximum_completion_overhead,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afc")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a lifecycle trace")
    validate.add_argument("trace")

    simulation = subparsers.add_parser("simulate", help="compare baseline and controller")
    simulation.add_argument("trace")
    simulation.add_argument("--idle-after", type=float, default=30.0)
    simulation.add_argument("--checkpoint-seconds", type=float, default=0.5)
    simulation.add_argument("--resume-seconds", type=float, default=1.5)
    simulation.add_argument("--cpu-hourly", type=float, default=0.0)
    simulation.add_argument("--memory-gib-hourly", type=float, default=0.0)
    simulation.add_argument("--minimum-savings", type=float, default=20.0)
    simulation.add_argument("--maximum-completion-overhead", type=float, default=5.0)
    simulation.add_argument("--pretty", action="store_true")

    generate = subparsers.add_parser("generate", help="create a deterministic synthetic trace")
    generate.add_argument("--agents", type=int, default=200)
    generate.add_argument("--seed", type=int, default=20261108)
    generate.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            generate_trace(args.output, args.agents, args.seed)
            print(json.dumps({"agents": args.agents, "output": args.output, "seed": args.seed}))
            return 0

        traces = load_trace(args.trace)
        if args.command == "validate":
            print(json.dumps({"agents": len(traces), "status": "valid", "trace": args.trace}))
            return 0

        result = simulate(traces, _policy_from_args(args))
        print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except (OSError, TraceValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
