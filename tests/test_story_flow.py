from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "Matrixapi-imagegen"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import generate  # noqa: E402


class SequentialStoryTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["generate.py", *argv]), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = generate.main()
        output = stdout.getvalue().strip() or stderr.getvalue().strip()
        return code, json.loads(output), stderr.getvalue()

    def test_three_pages_chain_all_references_then_only_previous_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            references = []
            for index in range(16):
                path = output_dir / f"reference-{index:02d}.png"
                Image.new("RGB", (64, 96), (index * 8, 30, 120)).save(path)
                references.append(str(path))

            page_files = []
            for index in range(1, 4):
                path = output_dir / f"page-{index}.png"
                Image.new("RGB", (64, 96), (20, index * 40, 80)).save(path)
                page_files.append(str(path))

            first_args = [
                "--task-id",
                "task-story-page-one-0001",
                "--story-pages",
                "3",
                "--prompt",
                "Fuse the references into one style and create a continuous battle story.",
                "--size",
                "4K",
                "--quality",
                "high",
                "--out-dir",
                str(output_dir),
            ]
            for reference in references:
                first_args.extend(["--image", reference])

            with (
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=(
                        "https://matrixapii.com",
                        "key",
                        "gpt-image-2",
                        "test",
                    ),
                ),
                patch.object(
                    generate,
                    "call_edit_api",
                    return_value={"data": [{"url": "https://example/image"}]},
                ) as call_edit,
                patch.object(
                    generate,
                    "wait_for_task",
                    side_effect=lambda result, *_args: result,
                ),
                patch.object(
                    generate,
                    "save_images",
                    side_effect=[[page_files[0]], [page_files[1]], [page_files[2]]],
                ),
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                first_code, first, _ = self._run(first_args)
                self.assertEqual(first_code, 0)
                self.assertEqual(first["story"]["page"], 1)
                self.assertEqual(first["story"]["status"], "active")

                second_code, second, _ = self._run(first["story"]["next_arguments"])
                self.assertEqual(second_code, 0)
                self.assertEqual(second["story"]["page"], 2)
                self.assertEqual(second["story"]["status"], "active")

                third_code, third, _ = self._run(second["story"]["next_arguments"])
                self.assertEqual(third_code, 0)
                self.assertEqual(third["story"]["page"], 3)
                self.assertEqual(third["story"]["status"], "completed")
                self.assertNotIn("next_arguments", third["story"])

            self.assertEqual(call_edit.call_count, 3)
            first_call, second_call, third_call = call_edit.call_args_list
            self.assertEqual(first_call.args[6], references)
            self.assertEqual(
                second_call.args[6], [Path(page_files[0]).resolve().as_posix()]
            )
            self.assertEqual(
                third_call.args[6], [Path(page_files[1]).resolve().as_posix()]
            )
            for index, api_call in enumerate(call_edit.call_args_list, start=1):
                self.assertEqual(api_call.args[2], "gpt-image-2")
                self.assertEqual(api_call.args[5], 1)
                self.assertEqual(api_call.args[4], "2560x3840")
                self.assertEqual(api_call.args[9]["quality"], "high")
                self.assertEqual(api_call.args[9]["aspect_ratio"], "2:3")
                self.assertEqual(api_call.args[9]["async"], "true")
                self.assertIn(f"comic page {index} of 3", api_call.args[3])
                self.assertIn("Full story request:", api_call.args[3])
                self.assertIn("No captions, speech bubbles", api_call.args[3])

            state = json.loads(
                Path(third["story"]["state_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["page"], 3)
            self.assertEqual(
                state["last_original_file"], Path(page_files[2]).resolve().as_posix()
            )

    def test_failed_page_stops_story_and_cannot_be_submitted_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            reference = output_dir / "reference.png"
            page_one = output_dir / "page-one.png"
            Image.new("RGB", (64, 96), "navy").save(reference)
            Image.new("RGB", (64, 96), "red").save(page_one)

            with (
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=(
                        "https://matrixapii.com",
                        "key",
                        "gpt-image-2",
                        "test",
                    ),
                ),
                patch.object(
                    generate,
                    "call_edit_api",
                    side_effect=[
                        {"data": [{"url": "https://example/page-one"}]},
                        generate.ImageGenError("page two failed"),
                    ],
                ) as call_edit,
                patch.object(
                    generate,
                    "wait_for_task",
                    side_effect=lambda result, *_args: result,
                ),
                patch.object(generate, "save_images", return_value=[str(page_one)]),
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                code, first, _ = self._run(
                    [
                        "--task-id",
                        "task-story-failure-0001",
                        "--story-pages",
                        "3",
                        "--prompt",
                        "A continuous visual story",
                        "--image",
                        str(reference),
                        "--out-dir",
                        str(output_dir),
                    ]
                )
                self.assertEqual(code, 0)
                next_arguments = first["story"]["next_arguments"]

                failed_code, failed, _ = self._run(next_arguments)
                self.assertEqual(failed_code, 1)
                self.assertIn("page two failed", failed["error"])

                retry_code, retry, _ = self._run(next_arguments)
                self.assertEqual(retry_code, 1)
                self.assertIn("status is failed", retry["error"])

            self.assertEqual(call_edit.call_count, 2)
            state = json.loads(
                Path(first["story"]["state_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "failed")
            self.assertIsNone(state["next_task_id"])


if __name__ == "__main__":
    unittest.main()
