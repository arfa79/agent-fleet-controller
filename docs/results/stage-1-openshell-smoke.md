# Stage 1 OpenShell smoke result

Date: 2026-11-09

## Scope

This disposable local test evaluated AFC's observe-only boundary against a real
OpenShell control plane. It did not modify OpenShell, enable lifecycle control,
use production credentials, or exercise a production environment.

## Environment

- OpenShell CLI and gateway: `0.1.7`
- Distribution artifacts: official release archives verified against their
  published SHA-256 checksum files
- Gateway backend: Docker `29.1.3` with a temporary SQLite database
- Host kernel: Linux `5.15.0-190-generic`
- Host Landlock support: ABI 1 from a direct `landlock_create_ruleset` probe
- `/dev/kvm`: unavailable

The gateway used temporary local JWT and TLS material and was stopped after the
test. No test credentials or raw recordings are committed to this repository.

## Results

The local OpenShell gateway became healthy, its Docker compute driver `0.1.7`
registered, `openshell status --output json` connected successfully, `/readyz`
reported healthy, and the gateway exposed Prometheus metrics.

AFC then wrapped the real `openshell status --output json` control-plane call.
The child returned `0`; AFC collected seven resource samples, rejected zero
markers, and produced a trace that passed `afc validate`. The lifecycle was:

```text
agent_started -> model_wait_started -> model_wait_finished -> agent_completed
```

This marker was deliberately emitted by the test harness around a safely idle
control-plane pause. It was not inferred from network activity or OpenShell
telemetry.

A second AFC run wrapped a real sandbox-creation attempt. OpenShell returned
`1`; AFC propagated that status, recorded `agent_failed`, and produced a failure
trace that also passed validation. Cleanup left no test sandboxes.

## Sandbox blocker

Sandbox provisioning failed with:

```text
ControlSupervisorStartFailed: Docker sandbox exited before supervisor became ready
```

The failure reproduced with `alpine:3.20` and `ubuntu:24.04`, at both 256 MiB
and 1 GiB memory allocations, and with loopback and Docker-bridge listener
arrangements. The host provides Landlock ABI 1, while OpenShell requires
Landlock ABI 3 or newer, introduced upstream in Linux 6.2. The MicroVM compute
driver is not an alternative on this host because `/dev/kvm` is absent.

OpenShell's current kernel requirements are documented in its
[support matrix](https://github.com/NVIDIA/OpenShell/blob/main/docs/about/support-matrix.mdx#kernel-requirements).
`openshell doctor check` passed on this incompatible host; this behavior is
already tracked in [OpenShell issue #4133](https://github.com/NVIDIA/OpenShell/issues/4133).

## Conclusion

The test validates that AFC can observe and validate a real OpenShell
control-plane lifecycle, including command failure propagation. It does not
validate sandbox execution, idle resource release, resume behavior, or savings.
No production-performance or cost claim can be made from this smoke test.

The next reproducible stage requires a disposable Linux 6.2+ host whose direct
Landlock probe reports ABI 3 or newer. On that host, repeat the same artifact
verification and gateway checks, create a sandbox, execute a sanitized workload,
and compare always-on and native OpenShell idle-stop baselines before testing
any AFC lifecycle adapter.
