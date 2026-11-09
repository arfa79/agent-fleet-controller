from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Sequence

from .markers import MARKER_KINDS, MARKER_SOCKET_ENV
from .model import EventKind, WAIT_FINISHES, WAIT_PAIRS


@dataclass(frozen=True, slots=True)
class ResourceSample:
    cpu_seconds: float
    rss_bytes: int
    processes: int


@dataclass(frozen=True, slots=True)
class RecordResult:
    agent_id: str
    returncode: int
    trace_path: Path
    metrics_path: Path
    samples: int
    rejected_markers: int


def _open_private_exclusive(path: Path) -> IO[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def _read_proc_stats(root_pid: int) -> ResourceSample:
    records: dict[int, tuple[int, float, int]] = {}
    clock_ticks = os.sysconf("SC_CLK_TCK")
    page_size = os.sysconf("SC_PAGE_SIZE")

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            tail = raw[raw.rfind(")") + 2 :].split()
            pid = int(entry.name)
            parent_pid = int(tail[1])
            cpu_seconds = (int(tail[11]) + int(tail[12])) / clock_ticks
            rss_bytes = max(0, int(tail[21])) * page_size
            records[pid] = (parent_pid, cpu_seconds, rss_bytes)
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
            continue

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent_pid, _, _) in records.items():
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True

    present = [records[pid] for pid in descendants if pid in records]
    return ResourceSample(
        cpu_seconds=sum(record[1] for record in present),
        rss_bytes=sum(record[2] for record in present),
        processes=len(present),
    )


def _write_json_line(stream: IO[str], payload: dict[str, object]) -> None:
    stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    stream.write("\n")
    stream.flush()


def _decode_marker(payload: bytes) -> EventKind:
    if len(payload) > 256:
        raise ValueError("marker is too large")
    raw = json.loads(payload.decode("ascii"))
    if not isinstance(raw, dict) or set(raw) != {"kind"}:
        raise ValueError("marker has an invalid shape")
    kind = EventKind(raw["kind"])
    if kind not in MARKER_KINDS:
        raise ValueError("marker kind is not agent-emittable")
    return kind


def _marker_transition(active_wait: EventKind | None, kind: EventKind) -> EventKind | None:
    if kind in WAIT_PAIRS:
        if active_wait is not None:
            raise ValueError("cannot start a nested wait")
        return kind
    expected = WAIT_FINISHES[kind]
    if active_wait is not expected:
        raise ValueError("wait finish does not match the active wait")
    return None


def _drain_markers(
    marker_socket: socket.socket,
    trace_stream: IO[str],
    agent_id: str,
    start: float,
    active_wait: EventKind | None,
) -> tuple[EventKind | None, int]:
    rejected = 0
    while True:
        try:
            payload = marker_socket.recv(512)
        except BlockingIOError:
            break
        try:
            kind = _decode_marker(payload)
            active_wait = _marker_transition(active_wait, kind)
        except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
            rejected += 1
            continue
        _write_json_line(
            trace_stream,
            {
                "v": 1,
                "ts": round(time.monotonic() - start, 6),
                "agent_id": agent_id,
                "kind": kind.value,
            },
        )
    return active_wait, rejected


def record_command(
    command: Sequence[str],
    *,
    trace_path: str | Path,
    metrics_path: str | Path,
    cpu_cores: float,
    memory_gib: float,
    workload_class: str | None = None,
    sample_interval: float = 1.0,
) -> RecordResult:
    """Run a command and record only lifecycle and aggregate /proc metadata."""

    if not command:
        raise ValueError("a command is required")
    if cpu_cores <= 0 or memory_gib <= 0:
        raise ValueError("cpu_cores and memory_gib must be greater than zero")
    if sample_interval <= 0:
        raise ValueError("sample_interval must be greater than zero")
    if workload_class is not None:
        from .trace import WORKLOAD_CLASS_PATTERN

        if WORKLOAD_CLASS_PATTERN.fullmatch(workload_class) is None:
            raise ValueError("workload_class must be a lowercase safe label")

    trace_destination = Path(trace_path)
    metrics_destination = Path(metrics_path)
    if trace_destination.resolve() == metrics_destination.resolve():
        raise ValueError("trace and metrics paths must be different")
    if trace_destination.exists() or metrics_destination.exists():
        raise FileExistsError("refusing to overwrite an existing recording")

    agent_id = uuid.uuid4().hex
    start = time.monotonic()
    rejected_markers = 0
    sample_count = 0
    active_wait: EventKind | None = None

    with tempfile.TemporaryDirectory(prefix="afc-recorder-") as socket_directory:
        os.chmod(socket_directory, 0o700)
        socket_path = str(Path(socket_directory) / "markers.sock")
        marker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        marker_socket.bind(socket_path)
        marker_socket.setblocking(False)

        trace_stream: IO[str] | None = None
        metrics_stream: IO[str] | None = None
        try:
            trace_stream = _open_private_exclusive(trace_destination)
            metrics_stream = _open_private_exclusive(metrics_destination)
            child_environment = os.environ.copy()
            child_environment[MARKER_SOCKET_ENV] = socket_path
            child_environment["AFC_AGENT_ID"] = agent_id
            process = subprocess.Popen(
                list(command),
                env=child_environment,
                start_new_session=True,
            )
        except Exception:
            marker_socket.close()
            if trace_stream is not None:
                trace_stream.close()
                trace_destination.unlink(missing_ok=True)
            if metrics_stream is not None:
                metrics_stream.close()
                metrics_destination.unlink(missing_ok=True)
            raise

        assert trace_stream is not None and metrics_stream is not None
        with trace_stream, metrics_stream, marker_socket:
            started_event: dict[str, object] = {
                "v": 1,
                "ts": 0.0,
                "agent_id": agent_id,
                "kind": EventKind.AGENT_STARTED.value,
                "cpu_cores": cpu_cores,
                "memory_gib": memory_gib,
            }
            if workload_class is not None:
                started_event["workload_class"] = workload_class
            _write_json_line(trace_stream, started_event)

            selector = selectors.DefaultSelector()
            selector.register(marker_socket, selectors.EVENT_READ)
            next_sample = start
            interrupted = False

            try:
                while process.poll() is None:
                    now = time.monotonic()
                    timeout = max(0.0, min(0.25, next_sample - now))
                    for _, _ in selector.select(timeout):
                        active_wait, rejected = _drain_markers(
                            marker_socket, trace_stream, agent_id, start, active_wait
                        )
                        rejected_markers += rejected

                    now = time.monotonic()
                    if now >= next_sample:
                        sample = _read_proc_stats(process.pid)
                        _write_json_line(
                            metrics_stream,
                            {
                                "v": 1,
                                "ts": round(now - start, 6),
                                "agent_id": agent_id,
                                "cpu_seconds": round(sample.cpu_seconds, 6),
                                "rss_bytes": sample.rss_bytes,
                                "processes": sample.processes,
                            },
                        )
                        sample_count += 1
                        next_sample = now + sample_interval
            except KeyboardInterrupt:
                interrupted = True
                os.killpg(process.pid, signal.SIGINT)

            returncode = process.wait()
            active_wait, rejected = _drain_markers(
                marker_socket, trace_stream, agent_id, start, active_wait
            )
            rejected_markers += rejected
            selector.close()
            terminal = (
                EventKind.AGENT_COMPLETED if returncode == 0 and not interrupted else EventKind.AGENT_FAILED
            )
            _write_json_line(
                trace_stream,
                {
                    "v": 1,
                    "ts": round(time.monotonic() - start, 6),
                    "agent_id": agent_id,
                    "kind": terminal.value,
                },
            )

    return RecordResult(
        agent_id=agent_id,
        returncode=returncode,
        trace_path=trace_destination,
        metrics_path=metrics_destination,
        samples=sample_count,
        rejected_markers=rejected_markers,
    )


def normalized_returncode(returncode: int) -> int:
    if returncode < 0:
        return 128 + abs(returncode)
    return min(returncode, 255)
