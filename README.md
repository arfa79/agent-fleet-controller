# Agent Fleet Controller

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

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
  latency;
- an observe-only process recorder with content-free model, tool, and human-wait
  markers and aggregate Linux resource samples;
- workload-class analysis with separate gates and p50/p95/p99 wait statistics;
- validated experiment manifests that pin provenance, runtime, baseline,
  evaluator, collection gates, and policy;
- best-effort consumed CPU and memory summaries from recorder metrics;
- deterministic, observe-only `would_suspend`/`would_resume` decision logs;
- bounded, interleaved collection of two or more external agent workload
  classes, with isolated per-run workspaces and fail-fast preflights;
- sync and async durable-supervisor helpers that emit waits only after a
  runtime confirms sandbox release;
- a dependency-free durable-workflow adapter contract with local checkpoint,
  sandbox-allocation, callback-idempotency, timeout, and error-path proofs; and
- an optional Temporal workflow bridge with durable callback signals,
  replay-safe orchestration, bounded activities, and worker-restart tests.

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

### Record a real workload

Install the package in a virtual environment, or prefix commands with
`PYTHONPATH=src` while developing. Recordings default to owner-only file
permissions and existing files are never overwritten.

```bash
mkdir -p recordings
afc record \
  --output recordings/coding-run.jsonl \
  --cpu-cores 2 \
  --memory-gib 4 \
  --name coding-agent \
  -- your-agent-command --its-argument
```

This creates:

- `recordings/coding-run.jsonl`: lifecycle events for the existing simulator;
- `recordings/coding-run.metrics.jsonl`: sampled aggregate CPU, RSS, and process
  counts for the observed process tree.

Instrument known waits without sending their content to AFC:

```python
from agent_fleet_controller.markers import wait

with wait("model"):
    response = call_model()

with wait("tool"):
    result = call_tool()
```

A wait marker is a reclaimability assertion: while it is active, no process in
the managed sandbox may need to keep running. Do not wrap sandbox creation,
`sandbox exec`, or any other operation that is still doing work in the sandbox
merely because the harness is blocked waiting for it. A tool wait is eligible
only when the tool runs outside the sandbox and the sandbox itself is idle.

An agent that cannot import the SDK may invoke `afc mark model-wait-started`
and `afc mark model-wait-finished` around the operation. Supported wait types
are `model`, `tool`, and `human`.

For a framework integration that checkpoints state and releases the sandbox
before marking the external operation, see the
[durable workflow integration](docs/integrations/durable-workflows.md). The
recommended production foundation is a durable workflow engine with
asynchronous activity completion; a normal in-process model call is not enough.

Install the optional Temporal bridge with:

```bash
python3 -m pip install 'agent-fleet-controller[temporal]'
```

The bridge is intentionally not imported by the base package, so AFC remains
dependency-free unless this integration is selected. See the
[Temporal adapter guide](docs/integrations/durable-workflows.md#temporal-adapter)
for worker registration, callback signaling, replay tests, and security limits.

The recorder passes through the child process's stdout and stderr and never
reads them. It does not inspect command-line arguments, environment values,
prompts, responses, files, or network traffic. Linux `/proc` samples are
best-effort observations, not billing-grade accounting.

Without installing the package:

```bash
PYTHONPATH=src python3 -m agent_fleet_controller.cli simulate \
  examples/demo-trace.jsonl --pretty
```

The default hypothesis gate requires at least 20% CPU and memory resource-time
savings and no more than 5% aggregate completion-time overhead.

Analyze a directory of recordings without allowing a strong workload class to
hide a weak one:

```bash
afc analyze recordings/ \
  --minimum-traces-per-class 30 \
  --minimum-workload-classes 2 \
  --pretty
```

When default sibling `*.metrics.jsonl` files exist, analysis validates and
summarizes their observed CPU, memory, process-count, and lifecycle coverage.
These local process observations remain supporting evidence, not proof that a
remote sandbox released resources.

Preview decisions without calling a sandbox runtime:

```bash
afc plan recordings/coding-run.jsonl --idle-after 30 --pretty
```

Run a reproducible experiment whose inputs and policy are pinned in one file:

```bash
afc experiment examples/experiment-manifest.json --pretty
```

See [experiment manifests](docs/experiments.md) for the strict schema and
evidence limitations.

Collect external agent runs without putting commands or prompt content into
AFC traces:

```bash
afc collect \
  --spec examples/collection-spec.json \
  --output recordings/real-agent-pilot \
  --workspace-root /tmp/afc-real-agent-workspaces \
  --runs-per-class 30
```

Replace the example commands with an explicitly authorized agent and sanitized
task set. Collection alternates workload classes, bounds every command with a
timeout, and stops on the first failed class preflight. See the
[real-agent collection guide](docs/collection.md).

For a local functional exercise using deliberately scaled timings:

```bash
PYTHONPATH=src python3 scripts/run_controlled_pilot.py \
  --output recordings/controlled-pilot
```

This controlled pilot does not run a real agent or sandbox and must not be used
as evidence of production savings.

## What comes next

1. Collect sanitized traces from at least two real agent workload classes.
2. Analyze wait distributions and consumed-resource observations, then compare
   modeled savings with native runtime behavior.
3. Validate dry-run decisions before adding any runtime adapter.
4. Test OpenShell and Kubernetes Agent Sandbox integrations.
5. Feed session and priority hints to an inference router.
6. Run a controlled 50-200-agent benchmark and publish reproducible results.

See [RFC 0001](docs/rfc/0001-agent-fleet-controller.md) and the
[validation plan](docs/validation.md) for the architecture and stage gates.
The [OpenShell integration guide](docs/integrations/openshell.md) describes the
current observe-only boundary and the evidence required before lifecycle control.
The [controlled local pilot](docs/results/stage-1-controlled-local.md) records
the first 30-by-2 functional result and its limitations.
The [OpenShell smoke result](docs/results/stage-1-openshell-smoke.md) records a
real OpenShell v0.1.7 control-plane integration and the host-kernel blocker that
prevented a sandbox benchmark.
The [agent CLI preflight](docs/results/stage-1-agent-preflight.md) records why a
30-by-2 real-agent collection could not honestly be completed on this host yet.

## Safety and project status

This is pre-alpha research software. It must not receive production credentials
or customer traces. Live suspension, Kubernetes mutation, and inference routing
are out of scope for the first milestone.

## License

Licensed under the [Apache License 2.0](LICENSE). This permissive license includes
an explicit patent grant and is compatible with commercial use and with major
projects in the agent-infrastructure ecosystem that use Apache-2.0.
