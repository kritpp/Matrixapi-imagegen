#!/usr/bin/env python3
"""Execute an explicit ordered image plan in one local process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


GENERATE = Path(__file__).resolve().with_name("generate.py")
MAX_SEQUENCE_TASKS = 20
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")
DISALLOWED_SEQUENCE_OPTIONS = {
    "--check-config",
    "--process-only",
    "--story-next",
    "--story-pages",
}


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _option_values(arguments: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, argument in enumerate(arguments):
        if argument == option:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                raise ValueError(f"{option} requires a value")
            values.append(arguments[index + 1])
        elif argument.startswith(f"{option}="):
            values.append(argument.split("=", 1)[1])
    return values


def _single_option_value(
    arguments: list[str], option: str, task_index: int
) -> str:
    values = _option_values(arguments, option)
    if len(values) != 1 or not values[0].strip():
        raise ValueError(f"sequence task {task_index} must contain exactly one {option}")
    return values[0].strip()


def _has_option(arguments: list[str], option: str) -> bool:
    return any(argument == option or argument.startswith(f"{option}=") for argument in arguments)


def _hide(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(["attrib", "+h", str(path)], check=False, capture_output=True)
    except OSError:
        pass


def _remove_plan(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _load_plan(path: Path) -> list[list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("sequence plan must be a UTF-8 JSON file") from exc
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_SEQUENCE_TASKS:
        raise ValueError(f"sequence plan must contain 1-{MAX_SEQUENCE_TASKS} tasks")
    normalized: list[list[str]] = []
    task_ids: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        if (
            not isinstance(task, list)
            or not task
            or not all(isinstance(value, str) for value in task)
        ):
            raise ValueError(f"sequence task {index} is invalid")
        for option in DISALLOWED_SEQUENCE_OPTIONS:
            if _has_option(task, option):
                raise ValueError(f"sequence task {index} cannot use {option}")

        task_id = _single_option_value(task, "--task-id", index)
        if TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise ValueError(f"sequence task {index} has an invalid --task-id")
        if task_id in task_ids:
            raise ValueError(f"sequence task {index} repeats --task-id {task_id}")
        task_ids.add(task_id)

        prompt_values = _option_values(task, "--prompt")
        prompt_file_values = _option_values(task, "--prompt-file")
        if bool(prompt_values) == bool(prompt_file_values):
            raise ValueError(
                f"sequence task {index} must contain exactly one of --prompt or --prompt-file"
            )
        selected_prompt = prompt_values or prompt_file_values
        if len(selected_prompt) != 1 or not selected_prompt[0].strip():
            raise ValueError(f"sequence task {index} has an invalid prompt argument")

        count_values = _option_values(task, "--n")
        if len(count_values) > 1 or (count_values and count_values[0] != "1"):
            raise ValueError(
                f"sequence task {index} must generate exactly one image; omit --n or use --n 1"
            )
        normalized.append(task)
    return normalized


def _result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    return None


def _result_error(result: dict[str, Any], expected_task_id: str) -> str | None:
    if result.get("ok") is not True:
        return str(result.get("error") or "sequence task failed")
    if result.get("task_id") != expected_task_id:
        return "sequence result task_id does not match the planned task"

    result_match = result.get("result_match")
    if not isinstance(result_match, dict) or result_match.get("task_id") != expected_task_id:
        return "sequence result_match does not match the planned task"

    started = result.get("request_started_at_ms")
    completed = result.get("completed_at_ms")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(completed, int)
        or isinstance(completed, bool)
        or completed < started
    ):
        return "sequence result timestamps are invalid"

    for field in ("preview_files", "download_files"):
        paths = result.get(field)
        if not isinstance(paths, list) or not paths or not all(
            isinstance(path, str) and path.strip() for path in paths
        ):
            return f"sequence result has no valid {field}"

    if result.get("idempotency_reused") is True and result.get(
        "reused_from_task_id"
    ) != expected_task_id:
        return "sequence result attempted to reuse another task's image"
    return None


def _run_tasks(tasks: list[list[str]]) -> int:
    completed: list[dict[str, Any]] = []
    for index, arguments in enumerate(tasks, start=1):
        expected_task_id = _single_option_value(arguments, "--task-id", index)
        process = subprocess.run(
            [sys.executable, str(GENERATE), *arguments],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        stdout = process.stdout or ""
        stderr = process.stderr or ""
        output = stdout + stderr
        result = _result(stdout)
        if process.returncode != 0:
            failure = _result(stderr) or result
            error = str((failure or {}).get("error") or "sequence task failed")
        elif result is None:
            error = "sequence task returned no terminal result"
        else:
            error = _result_error(result, expected_task_id)
        if error is not None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "failed_index": index,
                        "completed": completed,
                        "error": error,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if output:
                print(output[-2000:], file=sys.stderr)
            return 1
        assert result is not None
        item = {
            "index": index,
            "task_id": expected_task_id,
            "display_summary": result.get("display_summary") or "",
            "preview_files": result.get("preview_files") or [],
            "download_files": result.get("download_files") or [],
        }
        completed.append(item)
        print(json.dumps({"event": "sequence_item", **item}, ensure_ascii=False), flush=True)

    print(
        json.dumps(
            {
                "ok": True,
                "count": len(completed),
                "items": completed,
                "preview_files": [
                    path for item in completed for path in item["preview_files"]
                ],
                "download_files": [
                    path for item in completed for path in item["download_files"]
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def main() -> int:
    _configure_stdio()
    if len(sys.argv) != 3 or sys.argv[1] != "--plan-file":
        print("usage: sequence_runner.py --plan-file <UTF-8 JSON plan>", file=sys.stderr)
        return 2
    plan_path = Path(sys.argv[2]).expanduser()
    try:
        tasks = _load_plan(plan_path)
        _hide(plan_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    code = _run_tasks(tasks)
    _remove_plan(plan_path)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
