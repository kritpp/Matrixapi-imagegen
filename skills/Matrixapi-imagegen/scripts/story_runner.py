#!/usr/bin/env python3
"""Run every requested comic page sequentially without relying on Codex turns."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


GENERATE = Path(__file__).resolve().with_name("generate.py")


def _result_from_stdout(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    return None


def _run_page(arguments: list[str]) -> tuple[int, dict[str, Any] | None, str]:
    completed = subprocess.run(
        [sys.executable, str(GENERATE), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, _result_from_stdout(output), output


def main() -> int:
    initial = sys.argv[1:]
    if "--story-pages" not in initial:
        print("story_runner.py requires --story-pages", file=sys.stderr)
        return 2
    if "--story-next" in initial:
        print("story_runner.py starts a new story; use generate.py --story-next to recover one interrupted page", file=sys.stderr)
        return 2

    command = initial
    pages: list[dict[str, Any]] = []
    while True:
        code, result, raw_output = _run_page(command)
        if code != 0 or result is None or result.get("ok") is not True:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": (result or {}).get("error") or "Story page command failed",
                        "completed_pages": pages,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if raw_output:
                print(raw_output[-2000:], file=sys.stderr)
            return 1

        story = result.get("story") or {}
        page_payload = {
            "page": story.get("page"),
            "total_pages": story.get("total_pages"),
            "preview_files": result.get("preview_files") or [],
            "download_files": result.get("download_files") or [],
            "task_id": result.get("task_id"),
        }
        pages.append(page_payload)
        # A line is flushed after every successful paid page so a caller that
        # streams process output can display it before the next page begins.
        print(json.dumps({"event": "story_page", **page_payload}, ensure_ascii=False), flush=True)

        if story.get("status") == "completed":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "story": story,
                        "pages": pages,
                        "preview_files": [path for page in pages for path in page["preview_files"]],
                        "download_files": [path for page in pages for path in page["download_files"]],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0

        next_arguments = story.get("next_arguments")
        if not isinstance(next_arguments, list) or not all(isinstance(value, str) for value in next_arguments):
            print(json.dumps({"ok": False, "error": "Story returned no safe next page command", "completed_pages": pages}, ensure_ascii=False), flush=True)
            return 1
        command = next_arguments


if __name__ == "__main__":
    raise SystemExit(main())
