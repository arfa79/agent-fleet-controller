# Stage-1 controlled local functional pilot

Date: 2026-11-09

This exercise validates AFC's recorder, marker transport, multi-file analysis,
workload-class isolation, and collection gates. It is not a real-agent,
OpenShell, Kubernetes, or production benchmark.

## Setup

- 30 runs labeled `coding-agent` and 30 labeled `research-agent`;
- two content-free wait markers per run;
- 0.1-second `/proc` sampling interval;
- scaled policy: 0.05-second idle threshold, 0.005-second checkpoint, and
  0.01-second resume;
- hypothesis gate: at least 20% CPU and memory resource-time savings with at
  most 5% completion-time overhead.

The run is reproducible with:

```bash
PYTHONPATH=src python3 scripts/run_controlled_pilot.py \
  --output recordings/controlled-pilot \
  --runs-per-class 30
```

## Results

| Workload class | Runs | Waits | Wait p50 | Wait p95 | Modeled savings | Completion overhead | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `coding-agent` | 30 | 60 | 0.101 s | 0.126 s | 15.52% | 4.31% | Fail |
| `research-agent` | 30 | 60 | 0.212 s | 0.263 s | 47.14% | 3.40% | Pass |

All 60 lifecycle traces and 60 metrics files were produced successfully. The
collection gate passed because both named classes had 30 traces. The overall
hypothesis gate failed because the coding class did not meet the 20% savings
threshold. This is the intended behavior: a strong wait-heavy class cannot hide
a weak class.

## Interpretation

The result verifies analysis behavior and supports selective, class-specific
policy rather than a global idle rule. The percentages must not be cited as
expected infrastructure savings: waits and lifecycle costs were deliberately
scaled, the workloads were scripted, declared allocations drove the simulation,
and no sandbox runtime released resources.

The next evidence step is the same 30-by-2 collection using real agent harnesses
and an OpenShell native-runtime baseline.
