# Agent Fleet Controller

Agent Fleet Controller (AFC) is an evidence-first experiment for coordinating
agent sandboxes with inference infrastructure. The initial milestone does not
control a real cluster. It validates whether suspending idle agent sandboxes can
save enough CPU and memory to justify a production controller.

The intended integration boundary is:

```text
agent lifecycle events              inference/session hints
          |                                  |
          v                                  v
  Agent Fleet Controller --------------> Dynamo / vLLM / SGLang
          |
          v
  OpenShell / Kubernetes Agent Sandbox / another sandbox runtime
```

## Current milestone

The repository currently provides:

- a versioned JSONL trace format for agent lifecycle events;
- strict validation of ordering, state transitions, and resource declarations;
- a deterministic simulator comparing always-on sandboxes with idle suspension;
- explicit accounting for checkpoint cost, resume cost, and added latency;
- a seeded synthetic trace generator for repeatable local experiments;
- acceptance gates that prevent a resource-saving result from hiding excessive
  latency.

It intentionally does **not** claim real infrastructure savings yet. Synthetic
results are useful for testing the model and selecting thresholds, not for a
product or performance claim.

## Run it

Python 3.10+ and `pytest` are sufficient. The runtime has no third-party
dependencies.

```bash
make test
make validate
make demo
make benchmark
```

Without installing the package:

```bash
PYTHONPATH=src python3 -m agent_fleet_controller.cli simulate \
  examples/demo-trace.jsonl --pretty
```

The default hypothesis gate requires at least 20% CPU and memory resource-time
savings and no more than 5% aggregate completion-time overhead.

## What comes next

1. Replace synthetic traces with sanitized traces from real coding-agent runs.
2. Add a local process adapter that observes lifecycle events without taking
   privileged action.
3. Add suspend/resume adapters behind a dry-run-by-default interface.
4. Test OpenShell and Kubernetes Agent Sandbox integrations.
5. Feed session and priority hints to an inference router.
6. Run a controlled 50-200-agent benchmark and publish reproducible results.

See [RFC 0001](docs/rfc/0001-agent-fleet-controller.md) and the
[validation plan](docs/validation.md) for the architecture and stage gates.

## Safety and project status

This is pre-alpha research software. It must not receive production credentials
or customer traces. Live suspension, Kubernetes mutation, and inference routing
are out of scope for the first milestone. Licensing has intentionally not been
selected yet; choose and document a license before publishing or accepting
outside contributions.
