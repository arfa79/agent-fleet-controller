# OpenShell observe-only integration

OpenShell owns sandbox creation, resource limits, lifecycle transitions, and
future idle-stop behavior. AFC currently observes the external agent harness;
it does not patch OpenShell, delete pods, stop sandboxes, or duplicate the
OpenShell lifecycle controller.

## Current boundary

Run the harness that creates or controls an OpenShell sandbox under `afc record`:

```bash
afc record \
  --output recordings/openshell-coding-001.jsonl \
  --cpu-cores 2 \
  --memory-gib 4 \
  --workload-class coding-agent \
  -- python3 your_openshell_harness.py
```

The declared allocation must match the sandbox's OpenShell `--cpu` and
`--memory` configuration. Instrument blocking model, tool, and human waits in
the harness with `agent_fleet_controller.markers.wait`. The harness is the
authoritative source for semantic wait boundaries; network silence or an open
connection is not proof that a sandbox is idle.

The recorder's `/proc` metrics describe only the local harness process tree.
They do not describe a remote OpenShell sandbox. For Docker, Kubernetes, or VM
backends, consumed-resource evidence must come from the runtime's existing
metrics path and be joined by an opaque run ID outside AFC's lifecycle trace.

OpenShell OCSF logs are intentionally not imported as lifecycle markers. They
are best-effort security and activity telemetry, may contain operational
metadata, and do not provide the model/tool/human wait semantics required by
the simulator.

## Validation sequence

1. Record at least 30 sanitized runs from each of two real agent workload
   classes, including failures.
2. Run `afc analyze` and retain per-class results.
3. Measure the native OpenShell always-on and idle-stop baselines using the same
   workload, sandbox sizing, and evaluator.
4. Compare observed resource release and resume latency with AFC's assumptions.
5. Only then consider an opt-in, dry-run lifecycle adapter using OpenShell's
   official APIs.

The controlled local pilot in this repository tests the recording and analysis
pipeline only. It is not an OpenShell benchmark.
