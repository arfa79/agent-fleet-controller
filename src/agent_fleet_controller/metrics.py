from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class MetricsValidationError(ValueError):
    """Raised when recorder resource samples are malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class MetricsSample:
    timestamp: float
    agent_id: str
    cpu_seconds: float
    rss_bytes: int
    processes: int


def _finite_number(value: object, field: str, line_number: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricsValidationError(f"line {line_number}: {field} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise MetricsValidationError(f"line {line_number}: {field} must be finite")
    return parsed


def parse_metrics_jsonl(lines: Iterable[str]) -> dict[str, tuple[MetricsSample, ...]]:
    grouped: dict[str, list[MetricsSample]] = {}
    previous_timestamps: dict[str, float] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise MetricsValidationError(
                f"line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(raw, dict):
            raise MetricsValidationError(f"line {line_number}: sample must be a JSON object")
        expected = {"v", "ts", "agent_id", "cpu_seconds", "rss_bytes", "processes"}
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            unknown = sorted(set(raw) - expected)
            details = []
            if missing:
                details.append(f"missing fields: {', '.join(missing)}")
            if unknown:
                details.append(f"unknown fields: {', '.join(unknown)}")
            raise MetricsValidationError(f"line {line_number}: {'; '.join(details)}")
        if isinstance(raw["v"], bool) or raw["v"] != 1:
            raise MetricsValidationError(
                f"line {line_number}: only metrics version 1 is supported"
            )

        agent_id = raw["agent_id"]
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 128:
            raise MetricsValidationError(
                f"line {line_number}: agent_id must be a non-empty string of at most 128 characters"
            )
        timestamp = _finite_number(raw["ts"], "ts", line_number)
        cpu_seconds = _finite_number(raw["cpu_seconds"], "cpu_seconds", line_number)
        rss_bytes = raw["rss_bytes"]
        processes = raw["processes"]
        if timestamp < 0 or cpu_seconds < 0:
            raise MetricsValidationError(
                f"line {line_number}: ts and cpu_seconds must be non-negative"
            )
        if isinstance(rss_bytes, bool) or not isinstance(rss_bytes, int) or rss_bytes < 0:
            raise MetricsValidationError(
                f"line {line_number}: rss_bytes must be a non-negative integer"
            )
        if isinstance(processes, bool) or not isinstance(processes, int) or processes < 0:
            raise MetricsValidationError(
                f"line {line_number}: processes must be a non-negative integer"
            )
        previous = previous_timestamps.get(agent_id, -1.0)
        if timestamp < previous:
            raise MetricsValidationError(
                f"line {line_number}: timestamps for agent {agent_id!r} must be monotonic"
            )
        previous_timestamps[agent_id] = timestamp
        grouped.setdefault(agent_id, []).append(
            MetricsSample(
                timestamp=timestamp,
                agent_id=agent_id,
                cpu_seconds=cpu_seconds,
                rss_bytes=rss_bytes,
                processes=processes,
            )
        )

    if not grouped:
        raise MetricsValidationError("metrics file contains no samples")
    return {agent_id: tuple(samples) for agent_id, samples in grouped.items()}


def load_metrics(path: str | Path) -> dict[str, tuple[MetricsSample, ...]]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return parse_metrics_jsonl(stream)


def summarize_samples(
    samples: tuple[MetricsSample, ...], *, lifecycle_seconds: float
) -> dict[str, float | int]:
    if not samples:
        raise ValueError("at least one metrics sample is required")

    observed_cpu = samples[0].cpu_seconds
    memory_byte_seconds = 0.0
    for previous, current in zip(samples, samples[1:]):
        observed_cpu += max(0.0, current.cpu_seconds - previous.cpu_seconds)
        interval = current.timestamp - previous.timestamp
        memory_byte_seconds += interval * (previous.rss_bytes + current.rss_bytes) / 2.0

    observed_duration = samples[-1].timestamp - samples[0].timestamp
    coverage = 0.0 if lifecycle_seconds <= 0 else min(1.0, observed_duration / lifecycle_seconds)
    return {
        "samples": len(samples),
        "observed_duration_seconds": observed_duration,
        "lifecycle_coverage_percent": coverage * 100.0,
        "observed_cpu_seconds": observed_cpu,
        "observed_memory_gib_seconds": memory_byte_seconds / (1024.0**3),
        "peak_rss_bytes": max(sample.rss_bytes for sample in samples),
        "peak_processes": max(sample.processes for sample in samples),
    }
