from __future__ import annotations

import pytest

from agent_fleet_controller.trace import TraceValidationError, parse_jsonl


def test_parses_valid_trace() -> None:
    traces = parse_jsonl(
        [
            '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2}',
            '{"v":1,"ts":1,"agent_id":"a","kind":"model_wait_started"}',
            '{"v":1,"ts":5,"agent_id":"a","kind":"model_wait_finished"}',
            '{"v":1,"ts":6,"agent_id":"a","kind":"agent_completed"}',
        ]
    )
    assert len(traces) == 1
    assert traces[0].resources.cpu_cores == 1


def test_rejects_unmatched_wait() -> None:
    with pytest.raises(TraceValidationError, match="matching start"):
        parse_jsonl(
            [
                '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2}',
                '{"v":1,"ts":1,"agent_id":"a","kind":"tool_wait_finished"}',
                '{"v":1,"ts":2,"agent_id":"a","kind":"agent_completed"}',
            ]
        )


def test_rejects_unknown_fields() -> None:
    with pytest.raises(TraceValidationError, match="unknown fields"):
        parse_jsonl(
            [
                '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2,"secret":"no"}',
                '{"v":1,"ts":2,"agent_id":"a","kind":"agent_completed"}',
            ]
        )


def test_rejects_out_of_order_events() -> None:
    with pytest.raises(TraceValidationError, match="timestamps must be monotonic"):
        parse_jsonl(
            [
                '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2}',
                '{"v":1,"ts":10,"agent_id":"a","kind":"model_wait_started"}',
                '{"v":1,"ts":5,"agent_id":"a","kind":"model_wait_finished"}',
                '{"v":1,"ts":20,"agent_id":"a","kind":"agent_completed"}',
            ]
        )


def test_accepts_failure_while_waiting() -> None:
    traces = parse_jsonl(
        [
            '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2,"workload_class":"coding-agent"}',
            '{"v":1,"ts":1,"agent_id":"a","kind":"model_wait_started"}',
            '{"v":1,"ts":50,"agent_id":"a","kind":"agent_failed"}',
        ]
    )
    assert traces[0].workload_class == "coding-agent"


def test_rejects_completion_while_waiting() -> None:
    with pytest.raises(TraceValidationError, match="unclosed wait"):
        parse_jsonl(
            [
                '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2}',
                '{"v":1,"ts":1,"agent_id":"a","kind":"model_wait_started"}',
                '{"v":1,"ts":50,"agent_id":"a","kind":"agent_completed"}',
            ]
        )


def test_rejects_unsafe_workload_class() -> None:
    with pytest.raises(TraceValidationError, match="workload_class"):
        parse_jsonl(
            [
                '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":2,"workload_class":"Customer Name"}',
                '{"v":1,"ts":2,"agent_id":"a","kind":"agent_completed"}',
            ]
        )
