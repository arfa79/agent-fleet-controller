# Privacy-preserving workload recording

`afc record` is the Stage-1 observe-only adapter. It launches one foreground
command, passes through its output, records lifecycle markers, and samples
aggregate resource counters for the command's Linux process tree. It never
suspends, signals, throttles, or otherwise changes a normally running command,
except that `Ctrl-C` is forwarded to the isolated child process group.

## Usage

```bash
afc record \
  --output recordings/run-001.jsonl \
  --cpu-cores 2 \
  --memory-gib 4 \
  --workload-class coding-agent \
  --sample-interval 1 \
  -- your-agent-command
```

`--cpu-cores` and `--memory-gib` represent the resources allocated or reserved
for the sandbox. They are required because host capacity is not a defensible
substitute for workload allocation.

`--name` is an alias for `--workload-class`. It accepts only a lowercase safe
classification such as `coding-agent` or `tool-heavy`. Do not put customer,
user, repository, project, or task names in this field.

The metrics path defaults to the trace name with `.metrics.jsonl` replacing its
suffix. Use `--metrics` to select another path. AFC creates both files with mode
`0600` and refuses to overwrite either one.

## Lifecycle markers

Markers contain exactly one enumerated event kind. The recorder supplies the
opaque agent ID and receipt timestamp, so instrumented code cannot attach
prompts, responses, arguments, credentials, or arbitrary labels.

Python applications should use a context manager:

```python
from agent_fleet_controller.markers import wait

with wait("model"):
    model_result = model_client.generate()
```

Every started wait is an assertion that the managed sandbox is safely idle and
its resources could be reclaimed without preventing the enclosed operation
from completing. A blocked harness is not sufficient evidence. For example,
do not mark an OpenShell sandbox creation or `sandbox exec` call as a wait while
the sandbox is actively starting or executing. A tool wait is valid only when
the tool runs outside the sandbox and no sandbox process must continue.

Shell or other runtimes can use the CLI inherited by the recorded process:

```bash
afc mark tool-wait-started
run-external-tool
status=$?
afc mark tool-wait-finished
exit "$status"
```

Nested waits and unmatched finishes are rejected and counted in the recorder's
final summary. If the process fails during an active wait, the terminal failure
implicitly closes the wait for simulation purposes.

## Recorded fields

Lifecycle trace:

- schema version;
- monotonic seconds since recorder start;
- randomly generated opaque agent ID;
- enumerated lifecycle kind;
- declared CPU and memory allocation;
- optional safe workload class.

Metrics trace:

- schema version and opaque agent ID;
- monotonic seconds since recorder start;
- cumulative sampled CPU seconds;
- aggregate resident memory bytes;
- currently observed process count.

The resource sampler reads only `/proc/<pid>/stat`. It does not read process
command lines, environment variables, open files, network connections, or
process memory. Short-lived descendants may exit between samples, so these
metrics must be treated as best effort.

## Operational constraints

- Use disposable or explicitly authorized workloads.
- Store recordings outside Git; the repository ignores `recordings/`.
- Review and sanitize recordings before sharing them.
- Do not use production credentials or customer workloads during Stage 1.
- Keep the recorded command in the foreground; detached daemons are outside the
  recorder lifecycle.
- Emit wait markers only at boundaries where the sandbox is safely reclaimable;
  false wait markers make simulated savings invalid and would be unsafe input
  to a future lifecycle controller.
- A hard kill of the recorder may leave a partial trace without a terminal
  event. Validate every trace before simulation.
