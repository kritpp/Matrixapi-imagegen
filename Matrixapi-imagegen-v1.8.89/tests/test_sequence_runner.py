from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "sequence_runner.py"
SPEC = importlib.util.spec_from_file_location("matrixapi_sequence_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class SequenceRunnerTests(unittest.TestCase):
    def test_eight_item_plan_runs_all_eight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "roles.json"
            plan.write_text(
                json.dumps(
                    {
                        "tasks": [
                            ["--task-id", f"task-role-{index:02d}", "--prompt", f"角色 {index}"]
                            for index in range(1, 9)
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            completed = mock.Mock(returncode=0, stdout='{"ok":true,"preview_files":["x.png"],"download_files":["x.png"],"task_id":"x"}\n', stderr="")
            output = io.StringIO()
            with mock.patch.object(runner, "_hide"):
                with mock.patch.object(runner.subprocess, "run", return_value=completed) as run:
                    with mock.patch.object(sys, "argv", [str(SCRIPT), "--plan-file", str(plan)]):
                        with mock.patch("sys.stdout", output):
                            code = runner.main()
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 8)
        final = json.loads(output.getvalue().splitlines()[-1])
        self.assertTrue(final["ok"])
        self.assertEqual(final["count"], 8)


if __name__ == "__main__":
    unittest.main()
