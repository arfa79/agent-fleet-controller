# OpenShell lifecycle integration

OpenShell owns sandbox creation, resource limits, and lifecycle transitions.
AFC does not patch OpenShell or delete driver resources directly. Its opt-in
adapter calls OpenShell's native stop/start API and treats the gateway's
confirmed `Stopped` phase as the lifecycle boundary.

## Observe-only boundary

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

A marker means the OpenShell sandbox is safely reclaimable, not merely that the
harness is blocked. Never wrap `openshell sandbox create`, `openshell sandbox
exec`, or an equivalent API call in a wait marker while work is active in the
sandbox. A tool wait is valid only when the tool executes outside the sandbox
and no process inside the sandbox must continue. Model and human waits require
the same sandbox-idle guarantee.

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
5. Only then enable the opt-in lifecycle adapter against disposable sandboxes,
   first with a no-op external operation and driver-level allocation checks.

The controlled local pilot in this repository tests the recording and analysis
pipeline only. It is not an OpenShell benchmark.

The first real control-plane smoke test is documented in
[Stage 1 OpenShell smoke result](../results/stage-1-openshell-smoke.md). It
validated the observe-only boundary but did not produce sandbox resource-saving
evidence because the test host did not meet OpenShell's kernel requirements.

## Opt-in stop/start adapter

`OpenShellCheckpointAdapter` and `OpenShellSandboxRuntime` implement AFC's
durable workflow contracts around NVIDIA OpenShell's synchronous Python
`SandboxClient`. The client remains owned and authenticated by the application;
AFC never reads or stores its credentials.

The lifecycle is:

1. persist the application checkpoint outside the sandbox;
2. resolve the configured workflow-to-sandbox binding;
3. record the exact OpenShell sandbox ID while it is `Ready`;
4. call native `stop` and wait for `Stopped`;
5. independently read the phase and allow the durable external wait only when
   it is still `Stopped`;
6. call native `start` and wait for the same sandbox ID to become `Ready`; and
7. restore the durable application state into the new runtime generation.

OpenShell documents that stop removes the container, pod, or VM compute while
retaining the sandbox record and persistent workspace. Start creates a new
runtime generation. AFC therefore uses stop/start instead of sandbox deletion,
which would purge credentials and may destroy workspace state.

The contract was checked against NVIDIA OpenShell v0.1.7's
[sandbox lifecycle documentation](https://github.com/NVIDIA/OpenShell/blob/v0.1.7/docs/how-it-works/sandboxes/overview.mdx#stop-and-start-sandboxes)
and its synchronous Python `SandboxClient` stop/start/wait methods. Operators
must keep the SDK and gateway on compatible releases.

```python
from openshell import SandboxClient

from agent_fleet_controller.openshell import (
    OpenShellBinding,
    OpenShellCheckpointAdapter,
    OpenShellSandboxRuntime,
)

bindings = {
    "workflow-42": OpenShellBinding(
        name="agent-sandbox-42",
        workspace="default",
    )
}

client = SandboxClient.from_active_cluster()
checkpoints = OpenShellCheckpointAdapter(
    checkpoints=durable_agent_store,
    client=client,
    bindings=bindings,
)
runtime = OpenShellSandboxRuntime(
    client=client,
    bindings=bindings,
    application_restorer=agent_bootstrap,
    transition_timeout_seconds=300,
)
```

The application restorer is mandatory. A new OpenShell runtime generation does
not restore a Python process or in-memory agent loop. Use a scratch sandbox or a
bootstrap command that does not begin agent work until the durable checkpoint
has been restored. The application checkpoint must not depend only on process
memory or on storage that the selected OpenShell driver discards at stop. The
restorer must be idempotent because Temporal can retry an activity after its
side effect succeeds but before completion is recorded.

Bindings are declarative and keyed by AFC workflow ID. The adapter fails closed
if the name, workspace, or immutable sandbox ID changes; this prevents a retry
from stopping a same-name replacement. Client exceptions are redacted before
they can enter Temporal history or AFC logs.

OpenShell's Python SDK is not an AFC dependency. Install the official SDK that
matches the gateway release in the worker environment, then pass its
`SandboxClient` as shown above. This avoids coupling AFC's dependency-free base
package and Python 3.10 support to OpenShell's separate Python requirements.

## Validation status

The repository tests the complete adapter sequence with a contract fake,
including stopped-phase gating, idempotent release, sandbox-ID replacement,
Temporal JSON checkpoint conversion, provider errors, application restore, and
rollback to `Stopped` when restoration fails.

That is not yet a real resource-savings result. This host still cannot create an
OpenShell sandbox: its Linux 5.15 kernel exposes Landlock ABI 1, while OpenShell
requires Linux 6.2+ / Landlock ABI 3, and `/dev/kvm` is unavailable. A live
adapter test here would repeat the already documented provisioning failure.

The next controlled test must run on a disposable compatible host and prove all
of the following with driver-level evidence:

- the exact container, pod, or VM allocation exists before stop;
- OpenShell reaches `Stopped` and the allocation disappears;
- no allocation exists throughout the marked external wait;
- start creates a new runtime generation with the same sandbox ID;
- the application checkpoint is restored before agent work resumes; and
- allocated CPU/memory resource-time falls relative to an always-on baseline.
