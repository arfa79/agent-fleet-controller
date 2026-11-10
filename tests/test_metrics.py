from __future__ import annotations

import pytest

from agent_fleet_controller.metrics import (
    MetricsValidationError,
    parse_metrics_jsonl,
    summarize_samples,
)


def test_summarizes_best_effort_process_observations() -> None:
    grouped = parse_metrics_jsonl(
        [
            '{"v":1,"ts":0,"agent_id":"a","cpu_seconds":0.1,'
            '"rss_bytes":1073741824,"processes":1}',
            '{"v":1,"ts":2,"agent_id":"a","cpu_seconds":0.5,'
            '"rss_bytes":2147483648,"processes":2}',
            '{"v":1,"ts":4,"agent_id":"a","cpu_seconds":0.2,'
            '"rss_bytes":1073741824,"processes":1}',
        ]
    )

    summary = summarize_samples(grouped["a"], lifecycle_seconds=8)

    assert summary["observed_cpu_seconds"] == pytest.approx(0.5)
    assert summary["observed_memory_gib_seconds"] == pytest.approx(6.0)
    assert summary["lifecycle_coverage_percent"] == pytest.approx(50.0)
    assert summary["peak_processes"] == 2


def test_rejects_unknown_metrics_fields() -> None:
    with pytest.raises(MetricsValidationError, match="unknown fields"):
        parse_metrics_jsonl(
            [
                '{"v":1,"ts":0,"agent_id":"a","cpu_seconds":0,'
                '"rss_bytes":0,"processes":1,"command":"secret"}'
            ]
        )
