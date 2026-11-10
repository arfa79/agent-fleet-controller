from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import analyze_paths
from .experiment import ManifestValidationError, run_experiment
from .generator import generate_trace
from .markers import MarkerError, emit
from .metrics import MetricsValidationError
from .planning import plan
from .recorder import normalized_returncode, record_command
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


def _add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--idle-after", type=float, default=30.0)
    parser.add_argument("--checkpoint-seconds", type=float, default=0.5)
    parser.add_argument("--resume-seconds", type=float, default=1.5)
    parser.add_argument("--cpu-hourly", type=float, default=0.0)
    parser.add_argument("--memory-gib-hourly", type=float, default=0.0)
    parser.add_argument("--minimum-savings", type=float, default=20.0)
    parser.add_argument("--maximum-completion-overhead", type=float, default=5.0)
    parser.add_argument("--pretty", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afc")
    subparsers = parser.add_subparsers(dest="action", required=True)

    validate = subparsers.add_parser("validate", help="validate a lifecycle trace")
    validate.add_argument("trace")

    simulation = subparsers.add_parser("simulate", help="compare baseline and controller")
    simulation.add_argument("trace")
    _add_policy_arguments(simulation)

    analysis = subparsers.add_parser(
        "analyze", help="compare workload classes across trace files or directories"
    )
    analysis.add_argument("inputs", nargs="+")
    analysis.add_argument("--minimum-traces-per-class", type=int, default=30)
    analysis.add_argument("--minimum-workload-classes", type=int, default=2)
    _add_policy_arguments(analysis)

    planning = subparsers.add_parser(
        "plan", help="emit observe-only suspend/resume decisions without mutation"
    )
    planning.add_argument("trace")
    _add_policy_arguments(planning)

    experiment = subparsers.add_parser(
        "experiment", help="run a reproducible analysis from a versioned manifest"
    )
    experiment.add_argument("manifest")
    experiment.add_argument("--pretty", action="store_true")

    generate = subparsers.add_parser("generate", help="create a deterministic synthetic trace")
    generate.add_argument("--agents", type=int, default=200)
    generate.add_argument("--seed", type=int, default=20261108)
    generate.add_argument("--output", required=True)

    record = subparsers.add_parser(
        "record", help="observe one real command without suspending or mutating it"
    )
    record.add_argument("--output", required=True, help="destination lifecycle JSONL")
    record.add_argument("--metrics", help="destination resource-sample JSONL")
    record.add_argument("--cpu-cores", type=float, required=True)
    record.add_argument("--memory-gib", type=float, required=True)
    record.add_argument(
        "--name",
        "--workload-class",
        dest="workload_class",
        help="safe workload class such as coding-agent; never use a user or customer name",
    )
    record.add_argument("--sample-interval", type=float, default=1.0)
    record.add_argument("command", nargs=argparse.REMAINDER)

    marker = subparsers.add_parser("mark", help="emit a content-free lifecycle marker")
    marker.add_argument(
        "kind",
        choices=[
            "model-wait-started",
            "model-wait-finished",
            "tool-wait-started",
            "tool-wait-finished",
            "human-wait-started",
            "human-wait-finished",
        ],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "generate":
            generate_trace(args.output, args.agents, args.seed)
            print(json.dumps({"agents": args.agents, "output": args.output, "seed": args.seed}))
            return 0

        if args.action == "mark":
            emit(args.kind.replace("-", "_"))
            return 0

        if args.action == "record":
            command = list(args.command)
            if command and command[0] == "--":
                command = command[1:]
            output = Path(args.output)
            metrics = (
                Path(args.metrics)
                if args.metrics
                else output.with_suffix(".metrics.jsonl")
            )
            result = record_command(
                command,
                trace_path=output,
                metrics_path=metrics,
                cpu_cores=args.cpu_cores,
                memory_gib=args.memory_gib,
                workload_class=args.workload_class,
                sample_interval=args.sample_interval,
            )
            print(
                json.dumps(
                    {
                        "agent_id": result.agent_id,
                        "metrics": str(result.metrics_path),
                        "rejected_markers": result.rejected_markers,
                        "returncode": result.returncode,
                        "samples": result.samples,
                        "trace": str(result.trace_path),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return normalized_returncode(result.returncode)

        if args.action == "analyze":
            result = analyze_paths(
                args.inputs,
                _policy_from_args(args),
                minimum_traces_per_class=args.minimum_traces_per_class,
                minimum_workload_classes=args.minimum_workload_classes,
            )
            print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
            return 0

        if args.action == "experiment":
            result = run_experiment(args.manifest)
            print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
            return 0

        traces = load_trace(args.trace)
        if args.action == "validate":
            print(json.dumps({"agents": len(traces), "status": "valid", "trace": args.trace}))
            return 0

        result = (
            plan(traces, _policy_from_args(args))
            if args.action == "plan"
            else simulate(traces, _policy_from_args(args))
        )
        print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except (
        OSError,
        ManifestValidationError,
        MarkerError,
        MetricsValidationError,
        TraceValidationError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
