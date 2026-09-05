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
    / "sequence_runner.py"
)
SPEC = importlib.util.spec_from_file_location("matrixapi_sequence_runner", SCRIPT)
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


def _success(task_id: str) -> dict:
    return {
        "ok": True,
        "task_id": task_id,
        "request_started_at_ms": 100,
        "completed_at_ms": 101,
        "result_match": {"task_id": task_id},
        "display_summary": "实际尺寸：1672×941｜比例：16:9｜画质：high",
        "preview_files": [f"C:/generated/{task_id}.png"],
        "download_files": [f"C:/generated/{task_id}.png"],
    }


def _completed_process(payload: dict, returncode: int = 0) -> mock.Mock:
    target = "stdout" if returncode == 0 else "stderr"
    values = {"returncode": returncode, "stdout": "", "stderr": ""}
    values[target] = json.dumps(payload, ensure_ascii=False) + "\n"
    return mock.Mock(**values)


class SequenceRunnerTests(unittest.TestCase):
    def _write_plan(self, directory: str, tasks: list[list[str]]) -> Path:
        plan = Path(directory) / "roles.json"
        plan.write_text(
            json.dumps({"tasks": tasks}, ensure_ascii=False),
            encoding="utf-8",
        )
        return plan

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
        self, plan: Path, process: object
    ) -> tuple[int, _FlushTrackingStream, mock.Mock]:
        output = _FlushTrackingStream()
        error_output = io.StringIO()
        with mock.patch.object(runner, "_hide"), mock.patch.object(
            runner, "_configure_stdio"
        ), mock.patch.object(
            runner.subprocess, "run", side_effect=process
        ) as run, mock.patch.object(
            sys, "argv", [str(SCRIPT), "--plan-file", str(plan)]
        ), mock.patch(
            "sys.stdout", output
        ), mock.patch(
            "sys.stderr", error_output
        ):
            code = runner.main()
        return code, output, run

    def test_eight_item_plan_runs_all_eight_with_utf8_child_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks = [
                ["--task-id", f"task-role-{index:02d}", "--prompt", f"角色 {index}"]
                for index in range(1, 9)
            ]
            plan = self._write_plan(directory, tasks)

            def complete(command: list[str], **_kwargs: object) -> mock.Mock:
                return _completed_process(_success(_task_id(command)))

            code, output, run = self._run(plan, complete)
            self.assertFalse(plan.exists())

        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 8)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["encoding"], "utf-8")
            self.assertEqual(call.kwargs["errors"], "replace")
            self.assertTrue(call.kwargs["text"])
        lines = output.getvalue().splitlines()
        events = [json.loads(line) for line in lines[:-1]]
        self.assertEqual([event["event"] for event in events], ["sequence_item"] * 8)
        self.assertIn("1672×941", events[0]["display_summary"])
        final = json.loads(lines[-1])
        self.assertTrue(final["ok"])
        self.assertEqual(final["count"], 8)

    def test_each_item_is_flushed_before_the_next_process_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._write_plan(
                directory,
                [
                    ["--task-id", "task-first-01", "--prompt", "第一张"],
                    ["--task-id", "task-second-02", "--prompt", "第二张"],
                ],
            )
            output = _FlushTrackingStream()
            error_output = io.StringIO()
            call_count = 0

            def complete(command: list[str], **_kwargs: object) -> mock.Mock:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    self.assertTrue(
                        any(
                            '"event": "sequence_item"' in snapshot
                            and '"task_id": "task-first-01"' in snapshot
                            for snapshot in output.flush_snapshots
                        )
                    )
                return _completed_process(_success(_task_id(command)))

            with mock.patch.object(runner, "_hide"), mock.patch.object(
                runner, "_configure_stdio"
            ), mock.patch.object(
                runner.subprocess, "run", side_effect=complete
            ), mock.patch.object(
                sys, "argv", [str(SCRIPT), "--plan-file", str(plan)]
            ), mock.patch(
                "sys.stdout", output
            ), mock.patch(
                "sys.stderr", error_output
            ):
                code = runner.main()

        self.assertEqual(code, 0)
        self.assertEqual(call_count, 2)

    def test_invalid_plan_is_rejected_before_any_child_runs(self) -> None:
        cases = {
            "duplicate task id": [
                ["--task-id", "task-repeat-01", "--prompt", "one"],
                ["--task-id", "task-repeat-01", "--prompt", "two"],
            ],
            "invalid task id": [["--task-id", "short", "--prompt", "one"]],
            "two prompt sources": [
                [
                    "--task-id",
                    "task-prompts-01",
                    "--prompt",
                    "one",
                    "--prompt-file",
                    "prompt.txt",
                ]
            ],
            "multiple images per item": [
                ["--task-id", "task-count-001", "--prompt", "one", "--n", "2"]
            ],
            "story option": [
                [
                    "--task-id",
                    "task-story-001",
                    "--prompt",
                    "one",
                    "--story-pages",
                    "2",
                ]
            ],
        }
        for name, tasks in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                plan = self._write_plan(directory, tasks)
                with mock.patch.object(runner, "_configure_stdio"), mock.patch.object(
                    runner.subprocess, "run"
                ) as run, mock.patch.object(
                    sys, "argv", [str(SCRIPT), "--plan-file", str(plan)]
                ), mock.patch(
                    "sys.stderr", io.StringIO()
                ):
                    code = runner.main()
                self.assertEqual(code, 2)
                run.assert_not_called()
                self.assertTrue(plan.exists())

    def test_untrusted_or_stale_success_result_is_rejected(self) -> None:
        base = _success("task-current-01")
        cases: dict[str, dict] = {}
        cases["task id"] = dict(base, task_id="task-old-0001")
        cases["result match"] = dict(
            base, result_match={"task_id": "task-old-0001"}
        )
        cases["timestamps"] = dict(
            base, request_started_at_ms=200, completed_at_ms=100
        )
        cases["preview"] = dict(base, preview_files=[])
        cases["download"] = dict(base, download_files=[])
        cases["cross-task reuse"] = dict(
            base,
            idempotency_reused=True,
            reused_from_task_id="task-old-0001",
        )

        for name, payload in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                plan = self._write_plan(
                    directory,
                    [["--task-id", "task-current-01", "--prompt", "new image"]],
                )
                code, output, run = self._run(
                    plan, lambda *_args, **_kwargs: _completed_process(payload)
                )
                self.assertEqual(code, 1)
                self.assertEqual(run.call_count, 1)
                self.assertFalse(plan.exists())
                self.assertFalse(json.loads(output.getvalue().splitlines()[0])["ok"])

    def test_explicit_child_failure_stops_and_removes_loaded_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._write_plan(
                directory,
                [
                    ["--task-id", "task-failed-01", "--prompt", "one"],
                    ["--task-id", "task-never-002", "--prompt", "two"],
                ],
            )
            failure = {"ok": False, "error": "model service failed"}
            code, output, run = self._run(
                plan,
                lambda *_args, **_kwargs: _completed_process(
                    failure, returncode=1
                ),
            )
            self.assertFalse(plan.exists())

        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            json.loads(output.getvalue().splitlines()[0])["error"],
            "model service failed",
        )


if __name__ == "__main__":
    unittest.main()
