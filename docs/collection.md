# Real-agent trace collection

`afc collect` records two or more external agent workload classes from a strict
JSON specification. It is designed to make the 30-by-2 Stage-1 collection
repeatable without embedding agent-specific SDKs or credentials in AFC.

```bash
afc collect \
  --spec examples/collection-spec.json \
  --output recordings/real-agent-pilot \
  --workspace-root /tmp/afc-real-agent-workspaces \
  --runs-per-class 30 \
  --timeout-seconds 900 \
  --sample-interval 1
```

The collector:

- requires at least two safe workload-class labels;
- alternates classes by run number to reduce time-order bias;
- creates a separate workspace for every run;
- applies a bounded timeout and propagates command failure into the trace;
- stops after a failed first run instead of consuming the full task budget;
- creates recordings and the content-free status file with owner-only
  permissions; and
- does not copy commands, prompts, responses, stdout, or stderr into its status
  file or AFC traces.

The external command still receives its own normal arguments and passes its
stdout and stderr through the terminal. The collection spec itself contains the
command, and arguments may also be visible in the host process list. Do not put
secrets directly in either place. Agent output may contain response content or
session identifiers even though AFC does not retain it, so use the agent's
plain/minimal output mode where possible. Use the agent's approved credential
mechanism, sanitized public or synthetic tasks, and a disposable workspace.

## Marker requirement

An opaque agent command can provide useful lifecycle and local resource
observations, but it cannot establish reclaimable idle time. Do not wrap the
entire agent invocation in a wait marker: the agent process must remain running
to receive model responses and execute tools. Valid wait markers require an
agent/framework integration that knows no in-sandbox process must continue.

Until such an integration exists, retain marker-free real traces as workload
and resource evidence but do not use them to claim suspension savings. A
collection with zero waits is an honest negative result, not a reason to infer
waits from network silence or low CPU utilization.

## After collection

1. Validate and analyze the complete directory with `afc analyze`.
2. Preserve failed traces in the cohort.
3. Create an experiment manifest that pins the agent, model, tool environment,
   workload revision, evaluator, baseline, price table, and policy.
4. Run `afc plan` only after semantic reclaimability markers are available.
5. Never publish raw workspaces or recordings before review and sanitization.
