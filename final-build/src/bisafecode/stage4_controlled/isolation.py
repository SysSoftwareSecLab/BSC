"""Fail-closed subprocess isolation for future EXP-S4-002 phases.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The parent owns the wall-clock deadline and Raw stdout/stderr files.  The
worker owns method/oracle computation and reports its platform-normalized peak
RSS.  No formal entry point calls this module before the exact Mac gate opens.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping


POLL_SECONDS = 0.01


def ru_maxrss_to_bytes(value: int | float, *, platform_name: str | None = None) -> int:
    """Normalize getrusage.ru_maxrss on Linux and macOS.

    Linux reports KiB; macOS reports bytes.  The explicit platform argument
    exists for deterministic unit tests.
    """

    name = platform_name or sys.platform
    numeric = max(0, int(value))
    return numeric if name == "darwin" else numeric * 1024


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample_process_rss_bytes(pid: int) -> int:
    if sys.platform.startswith("linux"):
        status = Path(f"/proc/{pid}/status")
        try:
            values = {}
            for line in status.read_text(encoding="ascii").splitlines():
                if line.startswith(("VmHWM:", "VmRSS:")):
                    key, raw = line.split(":", 1)
                    values[key] = int(raw.strip().split()[0]) * 1024
            return max(values.values(), default=0)
        except (FileNotFoundError, PermissionError, ValueError):
            return 0
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ("ps", "-o", "rss=", "-p", str(pid)),
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
            )
            return int(completed.stdout.strip() or "0") * 1024
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0
    return 0


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    process.wait()


def _memory_limit_preexec(
    limit_bytes: int, *, platform_name: str | None = None
):
    """Return the Linux-only address-space limiter.

    macOS does not use ``RLIMIT_AS`` here: its Python/loader startup can fail
    before the worker protocol begins even when the resident set is far below
    the common budget.  The parent peak-RSS sampler remains authoritative on
    macOS.  Linux keeps both RLIMIT_AS and parent RSS enforcement.
    """

    name = platform_name or sys.platform
    if not name.startswith("linux"):
        return None

    def apply_limit() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))

    return apply_limit


def run_isolated_job(
    operation: str,
    payload: Mapping[str, Any],
    *,
    stdout_path: Path,
    stderr_path: Path,
    budget: Mapping[str, int | float],
    repository_root: Path,
) -> Mapping[str, Any]:
    """Execute one program operation in a fresh, hard-limited subprocess."""

    if operation not in {"method", "oracle", "external_oracle_r4", "ablation"}:
        raise ValueError("unsupported isolated operation")
    required_budget = {
        "wall_timeout_seconds",
        "max_resident_memory_bytes",
        "max_states",
        "max_transitions",
        "max_model_time_ns",
    }
    if set(budget) != required_budget:
        raise ValueError("isolated budget is incomplete or invalid")
    wall = budget["wall_timeout_seconds"]
    if isinstance(wall, bool) or not isinstance(wall, (int, float)) or wall < 0:
        raise ValueError("wall timeout is invalid")
    for key in required_budget - {"wall_timeout_seconds"}:
        value = budget[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("isolated budget is incomplete or invalid")
    for path in (stdout_path, stderr_path):
        if path.exists():
            raise FileExistsError(f"Raw worker log is create-once: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    timed_out = False
    memory_exhausted = False
    launch_failure: str | None = None
    peak_memory = 0
    response: Mapping[str, Any] | None = None
    exit_code: int | None = None
    with tempfile.TemporaryDirectory(prefix="bisafecode-s4-worker-") as directory:
        response_path = Path(directory) / "response.json"
        request = {
            "operation": operation,
            "payload": dict(payload),
            "budget": dict(budget),
            "response_path": str(response_path),
        }
        environment = dict(os.environ)
        src = str(repository_root / "src")
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
            process: subprocess.Popen[bytes] | None = None
            try:
                process = subprocess.Popen(
                    (sys.executable, "-m", "bisafecode.stage4_controlled.worker"),
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=environment,
                    start_new_session=True,
                    preexec_fn=_memory_limit_preexec(
                        int(budget["max_resident_memory_bytes"])
                    ),
                )
            except Exception as error:  # Popen and pre-exec failures are records.
                launch_failure = "SUBPROCESS_LAUNCH_FAILED"
                stderr_file.write(
                    f"{launch_failure}:{type(error).__name__}:{error}\n".encode(
                        "utf-8", errors="replace"
                    )
                )
            if process is not None:
                try:
                    if process.stdin is None:
                        raise BrokenPipeError("worker stdin pipe is absent")
                    process.stdin.write(
                        json.dumps(
                            request, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                    )
                    process.stdin.close()
                except Exception as error:
                    launch_failure = "SUBPROCESS_STDIN_FAILED"
                    stderr_file.write(
                        f"{launch_failure}:{type(error).__name__}:{error}\n".encode(
                            "utf-8", errors="replace"
                        )
                    )
                    try:
                        _terminate_process_group(process)
                    except (OSError, subprocess.SubprocessError):
                        pass
                if launch_failure is None:
                    deadline = started + float(wall)
                    if budget["wall_timeout_seconds"] == 0:
                        timed_out = True
                        _terminate_process_group(process)
                    else:
                        while process.poll() is None:
                            peak_memory = max(
                                peak_memory, _sample_process_rss_bytes(process.pid)
                            )
                            if peak_memory > budget["max_resident_memory_bytes"]:
                                memory_exhausted = True
                                _terminate_process_group(process)
                                break
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                timed_out = True
                                _terminate_process_group(process)
                                break
                            time.sleep(min(POLL_SECONDS, remaining))
                exit_code = process.poll()
        if response_path.is_file():
            try:
                parsed = json.loads(response_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    response = parsed
            except json.JSONDecodeError:
                response = None

    runtime_ms = (time.monotonic() - started) * 1000.0
    worker_peak = int((response or {}).get("peak_memory_bytes", 0) or 0)
    peak_memory = max(peak_memory, worker_peak)
    outcome = (response or {}).get("outcome")
    worker_status = (response or {}).get("worker_status")
    reasons = list((response or {}).get("reason_codes", ()))
    if launch_failure is not None:
        status = "exception"
        reasons = [launch_failure]
        outcome = None
    elif timed_out:
        status = "timeout"
        reasons = ["WALL_TIMEOUT_EXHAUSTED"]
        outcome = None
    elif memory_exhausted or worker_status == "resource_truncated":
        status = "resource_truncated"
        reasons = reasons or ["MEMORY_BUDGET_EXHAUSTED"]
        outcome = None
    elif exit_code != 0 or worker_status != "ok" or not isinstance(outcome, dict):
        status = "exception"
        reasons = reasons or [
            "WORKER_INITIALIZATION_FAILED" if response is None else "WORKER_EXCEPTION"
        ]
        outcome = None
    else:
        status = "ok"
        states = outcome.get("states")
        transitions = outcome.get("transitions")
        model_time = outcome.get("model_time_ns", 0)
        if isinstance(states, int) and states > budget["max_states"]:
            status, reasons = "resource_truncated", ["STATE_BUDGET_EXHAUSTED"]
        elif isinstance(transitions, int) and transitions > budget["max_transitions"]:
            status, reasons = "resource_truncated", ["TRANSITION_BUDGET_EXHAUSTED"]
        elif isinstance(model_time, int) and model_time > budget["max_model_time_ns"]:
            status, reasons = "resource_truncated", ["MODEL_TIME_BUDGET_EXHAUSTED"]
        elif peak_memory > budget["max_resident_memory_bytes"]:
            status, reasons = "resource_truncated", ["MEMORY_BUDGET_EXHAUSTED"]
    return {
        "status": status,
        "runtime_ms": runtime_ms,
        "peak_memory_bytes": peak_memory,
        "reason_codes": reasons,
        "outcome": outcome,
        "worker_exit_code": exit_code,
        "stdout_sha256": _sha256_file(stdout_path),
        "stderr_sha256": _sha256_file(stderr_path),
        "stdout_size_bytes": stdout_path.stat().st_size,
        "stderr_size_bytes": stderr_path.stat().st_size,
    }
