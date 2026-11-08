# RFC 0001: Agent Fleet Controller

- Status: Draft
- Date: 2026-11-08

## Desired outcome

Reduce the CPU and memory cost of large agent fleets without reducing task
success or causing unacceptable tail latency. The controller should remain
independent of a particular model, inference engine, sandbox, and Kubernetes
scheduler.

## Problem

Long-running agents alternate between local computation and waits for models,
tools, humans, rate limits, or dependent agents. Sandbox platforms understand
local resources and inference platforms understand GPU requests, but neither
necessarily owns the complete agent lifecycle. Keeping every sandbox fully
allocated during long waits wastes CPU and memory; reclaiming it carelessly can
lose state, break streams, discard useful caches, or increase completion time.

## Proposed boundary

AFC consumes lifecycle events and emits desired actions. Adapters, rather than
the decision engine, own platform-specific operations.

```text
Relay / framework hooks / OpenTelemetry
                  |
                  v
        normalized lifecycle events
                  |
                  v
       deterministic policy engine
          |                    |
          v                    v
 sandbox lifecycle adapter   inference hint adapter
          |                    |
 OpenShell / Agent Sandbox   Dynamo / vLLM / SGLang
```

The lifecycle event stream is the source of truth for decisions. Kubernetes is
the source of truth for actual workload state, while each inference backend
remains authoritative for GPU and KV-cache state. AFC must reconcile these
observations instead of inventing a parallel inventory.

## Initial policy

An agent becomes eligible for suspension after a configurable continuous wait.
The simulator charges checkpoint and resume overhead and rejects policies that
save resources only by introducing excessive completion latency.

Live implementations must add:

- hysteresis and bounded retries;
- idempotent action identifiers;
- optimistic concurrency against observed sandbox state;
- per-workload opt-in and a global kill switch;
- deadlines that prevent suspension near an SLO boundary;
- adapters that can report unsupported or unsafe suspension;
- recovery when checkpoint, suspend, or resume partially succeeds.

## Security properties

- Lifecycle events are untrusted and schema validated.
- Agent-provided labels cannot grant policy, credential, or tenant authority.
- AFC never receives model-provider or tool credentials.
- Actions are restricted to explicitly enrolled workloads.
- Audit records omit prompts, responses, tool arguments, and secrets by default.
- A live controller starts in observe-only mode.

## Stage gates

### Stage 0: trace simulator

Proceed when the model is deterministic, tested, and handles malformed traces.
Synthetic results do not establish commercial value.

### Stage 1: real trace collection

Collect sanitized traces from at least two distinct agent workloads. Proceed if
the estimated saving is at least 20% for both CPU and memory resource-time with
no more than 5% modeled completion-time overhead.

### Stage 2: local live experiment

Use disposable workloads and no production credentials. Measure real
checkpoint/resume reliability and compare observed values with the simulator.
Require zero lost work across fault-injection tests.

### Stage 3: controlled cluster experiment

Run 50-200 agents in a non-production cluster, initially observe-only and then
on an opt-in cohort. Compare cost per successful task, p95/p99 completion time,
success rate, and recovery rate against an always-on control group.

### Stage 4: product decision

Continue only if the controlled experiment shows a meaningful economic benefit
that is not already available through sandbox or inference autoscaling alone.

## Explicit non-goals

- Forking or modifying Linux.
- Replacing OpenShell, Agent Sandbox, Dynamo, vLLM, SGLang, or Kubernetes.
- Inspecting prompt or response content to make scheduling decisions.
- Claiming savings based only on synthetic traces.
- Mutating a production cluster during the validation phase.
