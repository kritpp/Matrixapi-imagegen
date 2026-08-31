#!/usr/bin/env python3
"""Execute an explicit ordered image plan in one local process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


GENERATE = Path(__file__).resolve().with_name("generate.py")
MAX_SEQUENCE_TASKS = 20


def _hide(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(["attrib", "+h", str(path)], check=False, capture_output=True)
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
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, list) or not task or not all(isinstance(value, str) for value in task):
            raise ValueError(f"sequence task {index} is invalid")
        if "--task-id" not in task or ("--prompt" not in task and "--prompt-file" not in task):
            raise ValueError(f"sequence task {index} must have a task id and prompt")
        if "--story-pages" in task or "--story-next" in task:
            raise ValueError("story tasks must use story_runner.py")
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


def main() -> int:
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

    completed: list[dict[str, Any]] = []
    for index, arguments in enumerate(tasks, start=1):
        process = subprocess.run(
            [sys.executable, str(GENERATE), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        output = (process.stdout or "") + (process.stderr or "")
        result = _result(output)
        if process.returncode != 0 or result is None or result.get("ok") is not True:
            print(json.dumps({"ok": False, "failed_index": index, "completed": completed, "error": (result or {}).get("error") or "sequence task failed"}, ensure_ascii=False), flush=True)
            if output:
                print(output[-2000:], file=sys.stderr)
            return 1
        item = {
            "index": index,
            "task_id": result.get("task_id"),
            "preview_files": result.get("preview_files") or [],
            "download_files": result.get("download_files") or [],
        }
        completed.append(item)
        print(json.dumps({"event": "sequence_item", **item}, ensure_ascii=False), flush=True)

    print(json.dumps({"ok": True, "count": len(completed), "items": completed, "preview_files": [path for item in completed for path in item["preview_files"]], "download_files": [path for item in completed for path in item["download_files"]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
