from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "Matrixapi-imagegen"
    / "scripts"
    / "story_runner.py"
)
SPEC = importlib.util.spec_from_file_location("matrixapi_story_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class _FlushTrackingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_snapshots: list[str] = []

    def flush(self) -> None:
        self.flush_snapshots.append(self.getvalue())
        super().flush()


def _task_id(command: list[str]) -> str:
    position = command.index("--task-id")
    return command[position + 1]


def _story_success(
    task_id: str,
    state_file: Path,
    page: int,
    total_pages: int,
    status: str,
    next_task_id: str | None = None,
) -> dict:
    story = {
        "state_file": state_file.resolve().as_posix(),
        "page": page,
        "total_pages": total_pages,
        "status": status,
    }
    if next_task_id is not None:
        story.update(
            {
                "next_task_id": next_task_id,
                "next_arguments": [
                    "--story-next",
                    state_file.resolve().as_posix(),
                    "--task-id",
                    next_task_id,
                ],
            }
        )
    return {
        "ok": True,
        "task_id": task_id,
        "request_started_at_ms": 100 + page,
        "completed_at_ms": 200 + page,
        "result_match": {"task_id": task_id},
        "display_summary": "实际尺寸：1672×941｜比例：2:3｜画质：high",
        "preview_files": [f"C:/generated/{task_id}.png"],
        "download_files": [f"C:/generated/{task_id}.png"],
        "story": story,
    }


def _completed_process(payload: dict, returncode: int = 0) -> mock.Mock:
    target = "stdout" if returncode == 0 else "stderr"
    values = {"returncode": returncode, "stdout": "", "stderr": ""}
    values[target] = json.dumps(payload, ensure_ascii=False) + "\n"
    return mock.Mock(**values)


class StoryRunnerTests(unittest.TestCase):
    def test_configures_both_runner_streams_as_utf8(self) -> None:
        stdout = mock.Mock()
        stderr = mock.Mock()
        with mock.patch.object(runner.sys, "stdout", stdout), mock.patch.object(
            runner.sys, "stderr", stderr
        ):
            runner._configure_stdio()
        stdout.reconfigure.assert_called_once_with(
            encoding="utf-8", errors="replace"
        )
        stderr.reconfigure.assert_called_once_with(
            encoding="utf-8", errors="replace"
        )

    def _run(
        self, arguments: list[str], process: object
    ) -> tuple[int, _FlushTrackingStream, mock.Mock]:
        output = _FlushTrackingStream()
        error_output = io.StringIO()
        with mock.patch.object(runner, "_configure_stdio"), mock.patch.object(
            runner.subprocess, "run", side_effect=process
        ) as run, mock.patch.object(
            sys, "argv", [str(SCRIPT), *arguments]
        ), mock.patch(
            "sys.stdout", output
        ), mock.patch(
            "sys.stderr", error_output
        ):
            code = runner.main()
        return code, output, run

    def test_pages_flush_before_next_page_and_final_state_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stories = Path(directory) / ".stories"
            stories.mkdir()
            state = stories / "story-current.json"
            lock = state.with_suffix(state.suffix + ".lock")
            other = stories / "story-other.json"
            other_lock = other.with_suffix(other.suffix + ".lock")
            for path in (state, lock, other, other_lock):
                path.write_text("state", encoding="utf-8")

            output = _FlushTrackingStream()
            error_output = io.StringIO()
            calls = 0

            def complete(command: list[str], **_kwargs: object) -> mock.Mock:
                nonlocal calls
                calls += 1
                if calls == 1:
                    payload = _story_success(
                        "task-story-01",
                        state,
                        1,
                        2,
                        "active",
                        "task-story-02",
                    )
                else:
                    self.assertEqual(
                        command[-4:],
                        [
                            "--story-next",
                            state.resolve().as_posix(),
                            "--task-id",
                            "task-story-02",
                        ],
                    )
                    self.assertTrue(
                        any(
                            '"event": "story_page"' in snapshot
                            and '"task_id": "task-story-01"' in snapshot
                            for snapshot in output.flush_snapshots
                        )
                    )
                    payload = _story_success(
                        "task-story-02", state, 2, 2, "completed"
                    )
                return _completed_process(payload)

            arguments = [
                "--task-id",
                "task-story-01",
                "--story-pages",
                "2",
                "--prompt",
                "连续故事",
            ]
            with mock.patch.object(runner, "_configure_stdio"), mock.patch.object(
                runner.subprocess, "run", side_effect=complete
            ) as run, mock.patch.object(
                sys, "argv", [str(SCRIPT), *arguments]
            ), mock.patch(
                "sys.stdout", output
            ), mock.patch(
                "sys.stderr", error_output
            ):
                code = runner.main()

            self.assertFalse(state.exists())
            self.assertFalse(lock.exists())
            self.assertTrue(other.exists())
            self.assertTrue(other_lock.exists())

        self.assertEqual(code, 0)
        self.assertEqual(calls, 2)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["encoding"], "utf-8")
            self.assertEqual(call.kwargs["errors"], "replace")
            self.assertTrue(call.kwargs["text"])
        lines = output.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])["event"], "story_page")
        self.assertEqual(json.loads(lines[1])["event"], "story_page")
        self.assertTrue(json.loads(lines[2])["ok"])
        self.assertIn("1672×941", lines[0])

    def test_invalid_initial_arguments_never_start_a_child(self) -> None:
        cases = {
            "missing task": ["--story-pages", "2", "--prompt", "story"],
            "invalid task": [
                "--task-id",
                "short",
                "--story-pages",
                "2",
                "--prompt",
                "story",
            ],
            "bad pages": [
                "--task-id",
                "task-story-01",
                "--story-pages",
                "1",
                "--prompt",
                "story",
            ],
            "two prompts": [
                "--task-id",
                "task-story-01",
                "--story-pages",
                "2",
                "--prompt",
                "story",
                "--prompt-file",
                "prompt.txt",
            ],
            "multiple images": [
                "--task-id",
                "task-story-01",
                "--story-pages",
                "2",
                "--prompt",
                "story",
                "--n",
                "2",
            ],
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                with mock.patch.object(runner, "_configure_stdio"), mock.patch.object(
                    runner.subprocess, "run"
                ) as run, mock.patch.object(
                    sys, "argv", [str(SCRIPT), *arguments]
                ), mock.patch(
                    "sys.stderr", io.StringIO()
                ):
                    code = runner.main()
                self.assertEqual(code, 2)
                run.assert_not_called()

    def test_untrusted_or_stale_page_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stories = Path(directory) / ".stories"
            stories.mkdir()
            state = stories / "story-current.json"
            state.write_text("state", encoding="utf-8")
            base = _story_success(
                "task-story-01",
                state,
                1,
                2,
                "active",
                "task-story-02",
            )
            cases: dict[str, dict] = {
                "task id": dict(base, task_id="task-old-0001"),
                "result match": dict(
                    base, result_match={"task_id": "task-old-0001"}
                ),
                "timestamps": dict(
                    base, request_started_at_ms=300, completed_at_ms=200
                ),
                "preview": dict(base, preview_files=[]),
                "download": dict(base, download_files=[]),
                "cross-task reuse": dict(
                    base,
                    idempotency_reused=True,
                    reused_from_task_id="task-old-0001",
                ),
            }
            arguments = [
                "--task-id",
                "task-story-01",
                "--story-pages",
                "2",
                "--prompt",
                "story",
            ]
            for name, payload in cases.items():
                with self.subTest(name=name):
                    code, output, run = self._run(
                        arguments,
                        lambda *_args, **_kwargs: _completed_process(payload),
                    )
                    self.assertEqual(code, 1)
                    self.assertEqual(run.call_count, 1)
                    self.assertFalse(json.loads(output.getvalue().splitlines()[0])["ok"])
                    self.assertTrue(state.exists())

    def test_failure_after_first_page_keeps_story_state_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stories = Path(directory) / ".stories"
            stories.mkdir()
            state = stories / "story-current.json"
            lock = state.with_suffix(state.suffix + ".lock")
            state.write_text("state", encoding="utf-8")
            lock.write_text("lock", encoding="utf-8")
            calls = 0

            def complete(_command: list[str], **_kwargs: object) -> mock.Mock:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return _completed_process(
                        _story_success(
                            "task-story-01",
                            state,
                            1,
                            2,
                            "active",
                            "task-story-02",
                        )
                    )
                return _completed_process(
                    {"ok": False, "error": "second page failed"}, returncode=1
                )

            code, output, run = self._run(
                [
                    "--task-id",
                    "task-story-01",
                    "--story-pages",
                    "2",
                    "--prompt",
                    "story",
                ],
                complete,
            )
            self.assertTrue(state.exists())
            self.assertTrue(lock.exists())

        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 2)
        lines = output.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])["event"], "story_page")
        self.assertEqual(json.loads(lines[1])["error"], "second page failed")


if __name__ == "__main__":
    unittest.main()
