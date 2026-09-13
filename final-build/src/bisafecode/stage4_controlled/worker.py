"""Internal isolated worker for future formal method and oracle calls.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import json
import resource
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping

from .isolation import ru_maxrss_to_bytes


def _method(payload: Mapping[str, Any], budget: Mapping[str, int]) -> Mapping[str, Any]:
    from .methods import run_method_in_process

    return run_method_in_process(
        str(payload["method_id"]),
        str(payload["source"]),
        program_id=str(payload["program_id"]),
        llm_binding=payload.get("llm_binding"),
        total_timeout_seconds=float(budget["wall_timeout_seconds"]),
    )


def _oracle(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from . import FORMAL_RAW_STATUS
    from .generator import TRAJECTORIES
    from .oracle import evaluate_program_oracle
    from .trace_extractor import extract_all_finite_traces

    source = str(payload["source"])
    program_id = str(payload["program_id"])
    durations = {
        value["sha256"]: int(value["duration_ns"])
        for value in TRAJECTORIES.values()
    }
    traces = extract_all_finite_traces(
        source,
        program_id=program_id,
        trajectory_durations_ns=durations,
    )
    result = evaluate_program_oracle(
        source,
        program_id=program_id,
        trajectory_durations_ns=durations,
        traces=traces,
    )
    def formal_raw(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (FORMAL_RAW_STATUS if key == "evidence_status" else formal_raw(item))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [formal_raw(item) for item in value]
        return value

    return {"traces": formal_raw(traces), "result": formal_raw(result), "states": 4, "transitions": 4, "model_time_ns": max(trace["terminal_time_ns"] for trace in traces)}


def _external_oracle_r4(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from . import FORMAL_RAW_STATUS
    from .generator import TRAJECTORIES
    from .trace_extractor import extract_all_finite_traces
    from bisafecode.stage4_external_blind.oracle_r4 import (
        evaluate_external_program_oracle_r4,
    )

    source = str(payload["source"])
    program_id = str(payload["program_id"])
    durations = {
        value["sha256"]: int(value["duration_ns"])
        for value in TRAJECTORIES.values()
    }
    traces = extract_all_finite_traces(
        source,
        program_id=program_id,
        trajectory_durations_ns=durations,
    )
    result = evaluate_external_program_oracle_r4(
        source,
        program_id=program_id,
        trajectory_durations_ns=durations,
        traces=traces,
    )

    def formal_raw(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (FORMAL_RAW_STATUS if key == "evidence_status" else formal_raw(item))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [formal_raw(item) for item in value]
        return value

    return {
        "traces": formal_raw(traces),
        "result": formal_raw(result),
        "states": 4,
        "transitions": 4,
        "model_time_ns": max(trace["terminal_time_ns"] for trace in traces),
    }


def _ablation(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from bisafecode.stage4_eval.ablation import run_ablation_variant

    return run_ablation_variant(
        str(payload["variant_id"]),
        str(payload["source"]),
        program_id=str(payload["program_id"]),
    )


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    response_path = Path(request["response_path"])
    try:
        operation = request["operation"]
        if operation == "method":
            outcome = _method(request["payload"], request["budget"])
        elif operation == "oracle":
            outcome = _oracle(request["payload"])
        elif operation == "external_oracle_r4":
            outcome = _external_oracle_r4(request["payload"])
        elif operation == "ablation":
            outcome = _ablation(request["payload"])
        else:
            raise ValueError("unknown worker operation")
        response = {
            "worker_status": "ok",
            "reason_codes": [],
            "outcome": outcome,
            "peak_memory_bytes": ru_maxrss_to_bytes(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            ),
        }
        print(f"worker-complete:{operation}")
    except MemoryError:
        response = {
            "worker_status": "resource_truncated",
            "reason_codes": ["MEMORY_BUDGET_EXHAUSTED"],
            "outcome": None,
            "peak_memory_bytes": ru_maxrss_to_bytes(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            ),
        }
        print("worker-resource-truncated:MemoryError", file=sys.stderr)
    except Exception as error:
        response = {
            "worker_status": "exception",
            "reason_codes": [f"EXCEPTION_{type(error).__name__.upper()}"],
            "outcome": None,
            "peak_memory_bytes": ru_maxrss_to_bytes(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            ),
        }
        print(f"worker-exception:{type(error).__name__}:{error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    response_path.write_text(
        json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
