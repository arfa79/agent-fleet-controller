# Durable workflow integration

An ordinary `await model_client.generate()` does not make an agent sandbox
reclaimable. The process must remain alive to own the connection, receive the
response, and continue the agent loop. Low CPU usage or network silence does
not change that requirement.

A reclaimable integration separates a durable supervisor from the managed
sandbox:

```text
managed sandbox       durable supervisor       external model or tool
      |                        |                           |
      | checkpoint state      |                           |
      |---------------------->|                           |
      | release confirmed     | submit durable request    |
      X                        |-------------------------->|
                               model_wait_started          |
                               |                    result |
                               |<--------------------------|
                               model_wait_finished         |
      |<----------------------| restore state + resume     |
```

The marker begins only after the runtime confirms that the sandbox has been
checkpointed and released. The response must be retrievable without keeping a
process in that sandbox alive. Completion then queues a new worker or restores
the released sandbox from durable state.

## AFC integration contract

`run_reclaimable_wait` and `run_reclaimable_wait_async` enforce the local
ordering contract:

1. persist a checkpoint;
2. request sandbox release and require the literal confirmation `True`;
3. emit the content-free wait-start marker;
4. wait through an external durable operation;
5. emit the wait-finished marker; and
6. restore the checkpoint even when the external operation fails.

```python
from agent_fleet_controller.durable import run_reclaimable_wait_async

result = await run_reclaimable_wait_async(
    "model",
    checkpoint=workflow_store.checkpoint,
    release=sandbox_runtime.release,
    external_wait=model_broker.wait_for_result,
    restore=sandbox_runtime.restore,
)
```

The checkpoint value remains inside the integration and is never included in
an AFC marker. These helpers must run in the durable supervisor, outside the
managed sandbox. They do not checkpoint processes or release infrastructure on
their own.

## Local framework proof

`DurableWorkflowRunner` provides typed adapter boundaries for a checkpoint
store, sandbox runtime, and durable external-operation broker. It enforces this
sequence:

1. validate identifiers and timeout before side effects;
2. create an opaque checkpoint;
3. require literal release confirmation and independently verify that the
   workflow has no sandbox allocation;
4. start the content-free AFC wait marker;
5. submit and retrieve the external operation by a stable, idempotency-capable
   operation ID; and
6. finish the marker and restore on either the success or error path.

The package includes in-memory adapters for local integration proofs. They
demonstrate that no sandbox allocation exists during the wait, duplicate
callbacks do not replace the first result, and provider failures or timeouts
restore the workflow on an explicit error path. They are test fakes, not
durable storage or a production runtime.

```python
from agent_fleet_controller.workflow import DurableWorkflowRunner

runner = DurableWorkflowRunner(
    checkpoints=checkpoint_adapter,
    runtime=sandbox_runtime,
    broker=operation_broker,
)
result = await runner.run(
    "model",
    workflow_id="workflow-42",
    operation_id="model-request-87",
    request=request,
    timeout_seconds=120,
)
```

The runner is deliberately dependency-free and does not claim crash-safe
workflow replay. A Temporal adapter or equivalent durable engine is still
required to survive a supervisor crash between these steps.

When `afc record` wraps that supervisor, its `/proc` metrics describe the
supervisor process tree, not a released remote sandbox. Use the sandbox
runtime's allocation and lifecycle evidence to prove real resource release;
keep the local samples only as supporting controller-overhead measurements.

## Recommended implementation foundation

For a production proof, use a durable workflow engine such as Temporal:

- workflow history is the durable source of agent state;
- an asynchronously completed activity submits the model request without
  retaining the agent worker;
- an external callback or small broker completes the activity;
- the sandbox adapter confirms release before AFC records a wait; and
- the resumed workflow starts a new sandbox incarnation from the checkpoint.

OpenAI Responses background mode can provide the durable model-operation side
of this design because the response is created asynchronously and can be
polled by ID. It does not checkpoint or release an agent sandbox by itself.
See the official OpenAI documentation:
<https://developers.openai.com/api/docs/guides/background>.

OpenAI Agents API is another option when the entire agent should run on hosted
infrastructure. In that case OpenAI manages the long-running task and saved
progress, leaving less sandbox lifecycle for AFC to control. See:
<https://developers.openai.com/api/docs/guides/agents>.

LangGraph checkpoints can also persist graph state, but a normal model node
that blocks a worker is not reclaimable. The deployment still needs a durable
request/callback boundary and a queue-backed resume path.

## Required evidence before runtime control

- checkpoint durability survives supervisor and worker restart;
- release confirmation comes from the runtime, not from the agent;
- duplicate callbacks and retries are idempotent;
- provider failure and timeout resume the workflow on an error path;
- the sandbox is absent during the marked wait;
- AFC traces contain no request IDs, prompts, responses, or checkpoint data;
  and
- a controlled benchmark measures actual allocated-resource reduction, not
  only process CPU usage.
