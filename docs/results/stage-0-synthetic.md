# Stage 0 synthetic validation result

- Date: 2026-11-08
- Dataset: 200 generated agents
- Generator seed: `20261108`
- Policy: suspend after 30 seconds of waiting
- Checkpoint overhead: 0.5 seconds
- Resume overhead: 1.5 seconds
- Evidence level: trace simulation only

## Result

| Metric | Result |
|---|---:|
| Completed traces | 190 |
| Failed traces | 10 |
| Suspension decisions | 509 |
| Modeled CPU resource-time saving | 67.66% |
| Modeled memory resource-time saving | 66.69% |
| Modeled completion-time overhead | 0.92% |
| Modeled cost saving | 67.47% |

The stage-0 model gate passed its 20% resource-saving and 5% completion-overhead
thresholds. This validates the simulator's accounting path, not the business
hypothesis. The generated wait-time distribution intentionally contains long
waits and cannot establish what real agent fleets will save.

## Reproduction

```bash
make test
make benchmark
```

The complete machine-readable report is generated at
`validation-results/synthetic-200-result.json` and is intentionally ignored by
Git because it is reproducible output.

## Required next evidence

Replace the generated trace with sanitized lifecycle traces from real agent
workloads. Preserve the always-on baseline, include failed-task resource cost,
and measure actual checkpoint and resume behavior before making a performance or
commercial claim.
