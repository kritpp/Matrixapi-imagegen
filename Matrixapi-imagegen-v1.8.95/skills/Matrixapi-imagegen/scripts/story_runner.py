#!/usr/bin/env python3
"""Run every requested comic page sequentially without relying on Codex turns."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


GENERATE = Path(__file__).resolve().with_name("generate.py")
MAX_STORY_PAGES = 20
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")


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


def _single_option_value(arguments: list[str], option: str) -> str:
    values = _option_values(arguments, option)
    if len(values) != 1 or not values[0].strip():
        raise ValueError(f"story command must contain exactly one {option}")
    return values[0].strip()


def _has_option(arguments: list[str], option: str) -> bool:
    return any(
        argument == option or argument.startswith(f"{option}=")
        for argument in arguments
    )


def _validate_initial_arguments(arguments: list[str]) -> tuple[str, int]:
    if _has_option(arguments, "--story-next"):
        raise ValueError(
            "story_runner.py starts a new story; use generate.py --story-next "
            "to recover one interrupted page"
        )
    for option in ("--check-config", "--process-only"):
        if _has_option(arguments, option):
            raise ValueError(f"story command cannot use {option}")

    task_id = _single_option_value(arguments, "--task-id")
    if TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("story command has an invalid --task-id")

    page_value = _single_option_value(arguments, "--story-pages")
    try:
        total_pages = int(page_value)
    except ValueError as exc:
        raise ValueError("--story-pages must be an integer") from exc
    if not 2 <= total_pages <= MAX_STORY_PAGES:
        raise ValueError(f"--story-pages must be between 2 and {MAX_STORY_PAGES}")

    prompt_values = _option_values(arguments, "--prompt")
    prompt_file_values = _option_values(arguments, "--prompt-file")
    if bool(prompt_values) == bool(prompt_file_values):
        raise ValueError(
            "story command must contain exactly one of --prompt or --prompt-file"
        )
    selected_prompt = prompt_values or prompt_file_values
    if len(selected_prompt) != 1 or not selected_prompt[0].strip():
        raise ValueError("story command has an invalid prompt argument")

    count_values = _option_values(arguments, "--n")
    if len(count_values) > 1 or (count_values and count_values[0] != "1"):
        raise ValueError("story pages generate one image each; omit --n or use --n 1")
    return task_id, total_pages


def _validate_next_arguments(
    arguments: Any,
    next_task_id: Any,
    seen_task_ids: set[str],
    expected_state_path: Path,
) -> tuple[list[str], str]:
    if not isinstance(arguments, list) or not all(
        isinstance(value, str) for value in arguments
    ):
        raise ValueError("Story returned no safe next page command")
    if len(arguments) != 4 or _has_option(arguments, "--story-pages"):
        raise ValueError("Story returned an unsafe next page command")
    task_id = _single_option_value(arguments, "--task-id")
    next_state_path = (
        Path(_single_option_value(arguments, "--story-next")).expanduser().resolve()
    )
    if next_state_path != expected_state_path:
        raise ValueError("Story next page state path does not match the current story")
    if TASK_ID_PATTERN.fullmatch(task_id) is None or task_id != next_task_id:
        raise ValueError("Story next page task id does not match its state")
    if task_id in seen_task_ids:
        raise ValueError("Story repeated a page task id")
    return arguments, task_id


def _validated_story_state_path(story: dict[str, Any]) -> Path:
    value = story.get("state_file")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Story returned no state file")
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() != ".json" or path.parent.name != ".stories":
        raise ValueError("Story returned an unsafe state file")
    return path


def _cleanup_story_state(path: Path) -> None:
    for target in (path, path.with_suffix(path.suffix + ".lock")):
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


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
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    result = _result_from_stdout(stdout)
    if completed.returncode != 0:
        result = _result_from_stdout(stderr) or result
    return completed.returncode, result, stdout + stderr


def _result_error(result: dict[str, Any], expected_task_id: str) -> str | None:
    if result.get("ok") is not True:
        return str(result.get("error") or "Story page command failed")
    if result.get("task_id") != expected_task_id:
        return "Story result task_id does not match the current page"

    result_match = result.get("result_match")
    if not isinstance(result_match, dict) or result_match.get("task_id") != expected_task_id:
        return "Story result_match does not match the current page"

    started = result.get("request_started_at_ms")
    completed = result.get("completed_at_ms")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(completed, int)
        or isinstance(completed, bool)
        or completed < started
    ):
        return "Story result timestamps are invalid"

    for field in ("preview_files", "download_files"):
        paths = result.get(field)
        if not isinstance(paths, list) or not paths or not all(
            isinstance(path, str) and path.strip() for path in paths
        ):
            return f"Story result has no valid {field}"

    if result.get("idempotency_reused") is True and result.get(
        "reused_from_task_id"
    ) != expected_task_id:
        return "Story result attempted to reuse another task's image"
    return None


def _emit_failure(error: str, pages: list[dict[str, Any]], raw_output: str = "") -> int:
    print(
        json.dumps(
            {"ok": False, "error": error, "completed_pages": pages},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if raw_output:
        print(raw_output[-2000:], file=sys.stderr)
    return 1


def main() -> int:
    _configure_stdio()
    initial = sys.argv[1:]
    try:
        expected_task_id, expected_total_pages = _validate_initial_arguments(initial)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    command = initial
    pages: list[dict[str, Any]] = []
    seen_task_ids = {expected_task_id}
    expected_page = 1
    story_state_path: Path | None = None
    while True:
        code, result, raw_output = _run_page(command)
        if code != 0:
            return _emit_failure(
                str((result or {}).get("error") or "Story page command failed"),
                pages,
                raw_output,
            )
        if result is None:
            return _emit_failure("Story page returned no terminal result", pages, raw_output)
        error = _result_error(result, expected_task_id)
        if error is not None:
            return _emit_failure(error, pages, raw_output)

        story = result.get("story")
        if not isinstance(story, dict):
            return _emit_failure("Story page returned no valid story state", pages)
        if (
            story.get("page") != expected_page
            or story.get("total_pages") != expected_total_pages
        ):
            return _emit_failure("Story page number or total does not match the request", pages)
        try:
            current_state_path = _validated_story_state_path(story)
        except ValueError as exc:
            return _emit_failure(str(exc), pages)
        if story_state_path is None:
            story_state_path = current_state_path
        elif current_state_path != story_state_path:
            return _emit_failure("Story state file changed between pages", pages)
        page_payload = {
            "page": expected_page,
            "total_pages": expected_total_pages,
            "display_summary": result.get("display_summary") or "",
            "preview_files": result.get("preview_files") or [],
            "download_files": result.get("download_files") or [],
            "task_id": expected_task_id,
        }
        pages.append(page_payload)
        # A line is flushed after every successful paid page so a caller that
        # streams process output can display it before the next page begins.
        print(
            json.dumps({"event": "story_page", **page_payload}, ensure_ascii=False),
            flush=True,
        )

        if expected_page == expected_total_pages:
            if story.get("status") != "completed":
                return _emit_failure("Story final page did not complete the story", pages)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "story": story,
                        "pages": pages,
                        "preview_files": [
                            path for page in pages for path in page["preview_files"]
                        ],
                        "download_files": [
                            path for page in pages for path in page["download_files"]
                        ],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            _cleanup_story_state(story_state_path)
            return 0

        if story.get("status") != "active":
            return _emit_failure("Story stopped before all requested pages completed", pages)
        try:
            command, expected_task_id = _validate_next_arguments(
                story.get("next_arguments"),
                story.get("next_task_id"),
                seen_task_ids,
                story_state_path,
            )
        except ValueError as exc:
            return _emit_failure(str(exc), pages)
        seen_task_ids.add(expected_task_id)
        expected_page += 1


if __name__ == "__main__":
    raise SystemExit(main())
