# Workload-class analysis

`afc analyze` loads lifecycle traces from one or more files or directories,
validates them, and evaluates each workload class independently. Files ending
in `.metrics.jsonl` are resource samples and are excluded automatically.

```bash
afc analyze recordings/ --pretty
```

The report includes:

- overall and per-class simulation gates;
- completed and failed trace counts;
- modeled CPU and memory resource-time savings;
- modeled completion-time overhead and estimated cost;
- model, tool, human, and combined wait distributions;
- p50, p95, p99, maximum, mean, count, and total wait duration;
- collection sufficiency checks.

The default collection gate requires at least two named workload classes and 30
traces in every class. Unclassified traces fail the collection gate. Duplicate
agent IDs across files are rejected instead of being silently combined.

The hypothesis gate passes only when the collection gate passes and every
workload class independently satisfies the configured savings and completion
overhead thresholds. An overall pass cannot hide a failing class.

Analysis remains trace simulation. It does not prove that a runtime can reclaim
the modeled resources or achieve the configured checkpoint and resume latency.
Validate those assumptions against a native-runtime baseline before making a
production or commercial claim.
