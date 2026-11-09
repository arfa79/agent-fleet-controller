from __future__ import annotations

import json
import socket

import pytest

from agent_fleet_controller.markers import MARKER_SOCKET_ENV, MarkerError, emit, wait


def test_emit_sends_only_the_marker_kind(tmp_path, monkeypatch) -> None:
    socket_path = tmp_path / "markers.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(socket_path))
    monkeypatch.setenv(MARKER_SOCKET_ENV, str(socket_path))
    try:
        emit("model_wait_started")
        assert json.loads(server.recv(256)) == {"kind": "model_wait_started"}
    finally:
        server.close()


def test_wait_context_emits_balanced_markers(tmp_path, monkeypatch) -> None:
    socket_path = tmp_path / "markers.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(socket_path))
    monkeypatch.setenv(MARKER_SOCKET_ENV, str(socket_path))
    try:
        with wait("tool"):
            pass
        received = [json.loads(server.recv(256))["kind"] for _ in range(2)]
        assert received == ["tool_wait_started", "tool_wait_finished"]
    finally:
        server.close()


def test_emit_requires_recorder_context(monkeypatch) -> None:
    monkeypatch.delenv(MARKER_SOCKET_ENV, raising=False)
    with pytest.raises(MarkerError, match="afc record"):
        emit("human_wait_started")
