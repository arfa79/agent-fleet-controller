from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, fields
from pathlib import Path

from .analysis import analyze_paths
from .simulation import SimulationPolicy


class ManifestValidationError(ValueError):
    """Raised when an experiment manifest is incomplete or unsafe to interpret."""


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
EVIDENCE_KINDS = {"synthetic", "controlled_local", "real_workload"}
BASELINE_KINDS = {"always_on", "native_runtime_policy"}


def _exact_object(value: object, expected: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field} must be an object")
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown: {', '.join(unknown)}")
        raise ManifestValidationError(f"{field} has invalid fields ({'; '.join(details)})")
    return value


def _safe_string(value: object, field: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ManifestValidationError(f"{field} must be a lowercase safe identifier")
    return value


def _metadata(value: object, field: str, expected: set[str]) -> dict[str, str]:
    raw = _exact_object(value, expected, field)
    return {key: _safe_string(raw[key], f"{field}.{key}") for key in sorted(expected)}


def load_manifest(path: str | Path) -> tuple[dict[str, object], SimulationPolicy, list[Path]]:
    manifest_path = Path(path)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"invalid manifest JSON: {exc.msg}") from exc

    expected = {
        "schema_version",
        "experiment_id",
        "evidence_kind",
        "runtime",
        "agent",
        "model",
        "tool_environment",
        "workload",
        "baseline",
        "evaluator",
        "collection",
        "policy",
    }
    manifest = _exact_object(raw, expected, "manifest")
    if manifest["schema_version"] != 1:
        raise ManifestValidationError("only manifest schema_version 1 is supported")

    experiment_id = _safe_string(manifest["experiment_id"], "experiment_id")
    evidence_kind = manifest["evidence_kind"]
    if evidence_kind not in EVIDENCE_KINDS:
        raise ManifestValidationError(
            f"evidence_kind must be one of: {', '.join(sorted(EVIDENCE_KINDS))}"
        )
    runtime = _metadata(manifest["runtime"], "runtime", {"name", "version", "driver"})
    agent = _metadata(manifest["agent"], "agent", {"name", "version"})
    model = _metadata(manifest["model"], "model", {"provider", "model_id"})
    tool_environment = _metadata(
        manifest["tool_environment"], "tool_environment", {"name", "revision"}
    )
    workload_raw = _exact_object(
        manifest["workload"],
        {"dataset_id", "revision", "real_agent_workload"},
        "workload",
    )
    workload = {
        "dataset_id": _safe_string(workload_raw["dataset_id"], "workload.dataset_id"),
        "revision": _safe_string(workload_raw["revision"], "workload.revision"),
        "real_agent_workload": workload_raw["real_agent_workload"],
    }
    if not isinstance(workload["real_agent_workload"], bool):
        raise ManifestValidationError("workload.real_agent_workload must be a boolean")
    if (evidence_kind == "real_workload") != workload["real_agent_workload"]:
        raise ManifestValidationError(
            "evidence_kind and workload.real_agent_workload disagree"
        )

    baseline_raw = _exact_object(
        manifest["baseline"],
        {"kind", "native_policy_enabled", "price_table_revision"},
        "baseline",
    )
    baseline_kind = baseline_raw["kind"]
    if baseline_kind not in BASELINE_KINDS:
        raise ManifestValidationError(
            f"baseline.kind must be one of: {', '.join(sorted(BASELINE_KINDS))}"
        )
    if not isinstance(baseline_raw["native_policy_enabled"], bool):
        raise ManifestValidationError("baseline.native_policy_enabled must be a boolean")
    if (baseline_kind == "native_runtime_policy") != baseline_raw["native_policy_enabled"]:
        raise ManifestValidationError("baseline kind and native_policy_enabled disagree")
    baseline = {
        "kind": baseline_kind,
        "native_policy_enabled": baseline_raw["native_policy_enabled"],
        "price_table_revision": _safe_string(
            baseline_raw["price_table_revision"], "baseline.price_table_revision"
        ),
    }
    evaluator = _metadata(manifest["evaluator"], "evaluator", {"name", "version"})

    collection_raw = _exact_object(
        manifest["collection"],
        {"inputs", "minimum_traces_per_class", "minimum_workload_classes"},
        "collection",
    )
    inputs = collection_raw["inputs"]
    if not isinstance(inputs, list) or not inputs:
        raise ManifestValidationError("collection.inputs must be a non-empty array")
    resolved_inputs: list[Path] = []
    for index, value in enumerate(inputs):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ManifestValidationError(
                f"collection.inputs[{index}] must be a non-empty path string"
            )
        candidate = Path(value)
        resolved_inputs.append(
            candidate if candidate.is_absolute() else manifest_path.parent / candidate
        )
    minimum_traces = collection_raw["minimum_traces_per_class"]
    minimum_classes = collection_raw["minimum_workload_classes"]
    for name, value in (
        ("minimum_traces_per_class", minimum_traces),
        ("minimum_workload_classes", minimum_classes),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ManifestValidationError(f"collection.{name} must be a positive integer")

    policy_fields = {field.name for field in fields(SimulationPolicy)}
    policy_raw = _exact_object(manifest["policy"], policy_fields, "policy")
    policy_values: dict[str, float] = {}
    for name in sorted(policy_fields):
        value = policy_raw[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ManifestValidationError(f"policy.{name} must be a number")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ManifestValidationError(f"policy.{name} must be finite")
        policy_values[name] = parsed
    policy = SimulationPolicy(**policy_values)
    policy.validate()

    normalized = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "evidence_kind": evidence_kind,
        "runtime": runtime,
        "agent": agent,
        "model": model,
        "tool_environment": tool_environment,
        "workload": workload,
        "baseline": baseline,
        "evaluator": evaluator,
        "collection": {
            "inputs": inputs,
            "minimum_traces_per_class": minimum_traces,
            "minimum_workload_classes": minimum_classes,
        },
        "policy": asdict(policy),
    }
    return normalized, policy, resolved_inputs


def run_experiment(path: str | Path) -> dict[str, object]:
    manifest, policy, inputs = load_manifest(path)
    collection = manifest["collection"]
    assert isinstance(collection, dict)
    report = analyze_paths(
        inputs,
        policy,
        minimum_traces_per_class=int(collection["minimum_traces_per_class"]),
        minimum_workload_classes=int(collection["minimum_workload_classes"]),
    )
    report["experiment"] = manifest
    return report
