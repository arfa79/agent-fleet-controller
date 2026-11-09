# Validation plan

## Primary hypothesis

For agent workloads with long external waits, reclaiming sandbox CPU and memory
can reduce cost per successfully completed task by at least 20% without reducing
task success and without increasing aggregate completion time by more than 5%.

## Required baselines

1. Always-on sandbox resources.
2. Native sandbox/runtime autoscaling with AFC disabled.
3. AFC using the same workload, model, tool environment, and resource limits.

The native-runtime baseline is essential: AFC has no value if an existing
platform already achieves the same result.

## Measurements

- Successful tasks and failed tasks, defined by an external evaluator.
- End-to-end p50, p95, and p99 completion time.
- Allocated and consumed CPU core-seconds.
- Allocated and consumed memory GiB-seconds.
- GPU seconds and inference request latency.
- Suspend, checkpoint, restore, and resume counts and failures.
- Lost or repeated tool actions after recovery.
- Cost per successful task using a versioned price table.

## Trace handling

Collect lifecycle timestamps, opaque agent identifiers, event kinds, and
resource quantities. Do not collect prompts, responses, source code, tool
arguments, credentials, personal data, or customer identifiers. Rotate opaque
identifiers before traces leave their originating environment.

## Anti-gaming rules

- Do not count failed tasks as resource savings.
- Report all configured thresholds and excluded traces.
- Keep the evaluation workload fixed before selecting the final policy.
- Separate synthetic, replayed, and live results.
- Report confidence intervals and all regressions, not only averages.
- Use the same task success evaluator for every cohort.

## Reproduce the stage-0 experiment

```bash
make test
make benchmark
cat validation-results/synthetic-200-result.json
```

The generated dataset uses a fixed seed. Its output verifies determinism and
accounting only. It is not evidence that real agents exhibit the same wait-time
distribution.

## Stage-1 collection

Use the observe-only recorder described in [recording.md](recording.md). Collect
at least 30 completed tasks from each of two workload classes, while retaining
failed traces in cost accounting. Validate every lifecycle trace before running
the simulator:

```bash
afc validate recordings/run-001.jsonl
afc simulate recordings/run-001.jsonl --pretty
```

Resource samples are supporting evidence and are currently analyzed separately
from allocation-based simulation. They are best-effort `/proc` observations and
must not be presented as billing-grade counters.
