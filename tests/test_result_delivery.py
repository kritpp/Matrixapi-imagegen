from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
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
import hide_result  # noqa: E402


class ResultDeliveryTests(unittest.TestCase):
    def test_imagegen_key_always_uses_the_compiled_api_url(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "IMAGEGEN_API_KEY": "test-key",
                    "IMAGEGEN_BASE_URL": "https://legacy.example.invalid",
                    "IMAGEGEN_MODEL": "gpt-image-2",
                },
                clear=True,
            ),
            patch.object(
                generate,
                "_environment_value",
                side_effect=lambda name: os.environ.get(name, ""),
            ),
            patch.object(generate, "_skill_env_file_values", return_value={}),
            patch.object(generate, "_user_environment_value", return_value=""),
        ):
            base_url, key, model, source = generate.discover_credentials()
        self.assertEqual(base_url, "https://eos.manyuvip.com")
        self.assertEqual(key, "test-key")
        self.assertEqual(model, "gpt-image-2")
        self.assertEqual(source, "environment")

    def test_installer_env_file_supplies_key_and_model_but_not_url(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                generate,
                "_skill_env_file_values",
                return_value={
                    "IMAGEGEN_API_KEY": "file-key",
                    "IMAGEGEN_MODEL": "gpt-image-2-pro",
                    "IMAGEGEN_BASE_URL": "https://ignored.example.invalid",
                },
            ),
            patch.object(generate, "_environment_value", return_value=""),
            patch.object(generate, "_user_environment_value", return_value=""),
        ):
            base_url, key, model, source = generate.discover_credentials()
        self.assertEqual(base_url, "https://eos.manyuvip.com")
        self.assertEqual(key, "file-key")
        self.assertEqual(model, "gpt-image-2-pro")
        self.assertEqual(source, "environment")

    def test_task_id_is_filename_safe_and_unique_when_generated(self) -> None:
        first = generate.normalize_task_id()
        second = generate.normalize_task_id()
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("task-"))
        self.assertEqual(
            generate.normalize_task_id("task-current-conversation-0001"),
            "task-current-conversation-0001",
        )
        with self.assertRaises(generate.ImageGenError):
            generate.normalize_task_id("bad id")

    def test_current_task_json_is_atomic_and_matches_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            image = output_dir / "current.png"
            image.write_bytes(b"image")
            task_id = "task-current-00000001"
            started = time.time_ns() // 1_000_000 - 1000
            stdout = io.StringIO()
            with patch.object(generate, "_schedule_result_hide", return_value=True), redirect_stdout(stdout):
                result = generate.emit_success(
                    {
                        "ok": True,
                        "preview_files": [image.as_posix()],
                        "download_files": [image.as_posix()],
                    },
                    output_dir,
                    task_id,
                    started,
                )

            emitted = json.loads(stdout.getvalue())
            record = Path(result["result_file"])
            persisted = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(emitted, persisted)
            self.assertEqual(emitted["task_id"], task_id)
            self.assertEqual(emitted["result_match"]["task_id"], task_id)
            self.assertEqual(emitted["request_started_at_ms"], started)
            self.assertGreaterEqual(emitted["completed_at_ms"], started)
            self.assertEqual(list(output_dir.glob("*.tmp")), [])

    def test_parallel_task_ids_cannot_share_a_result_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch.object(generate, "_schedule_result_hide", return_value=True), redirect_stdout(io.StringIO()):
                first = generate.emit_success(
                    {"ok": True, "preview_files": ["first.png"]},
                    output_dir,
                    "task-conversation-a-0001",
                    1000,
                )
                second = generate.emit_success(
                    {"ok": True, "preview_files": ["second.png"]},
                    output_dir,
                    "task-conversation-b-0001",
                    1001,
                )
            self.assertNotEqual(first["result_file"], second["result_file"])
            self.assertEqual(
                json.loads(Path(first["result_file"]).read_text(encoding="utf-8"))["preview_files"],
                ["first.png"],
            )
            self.assertEqual(
                json.loads(Path(second["result_file"]).read_text(encoding="utf-8"))["preview_files"],
                ["second.png"],
            )

    def test_reused_task_id_is_rejected_before_an_api_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            task_id = "task-already-used-0001"
            generate.result_record_path(output_dir, task_id).write_text("{}", encoding="utf-8")
            with self.assertRaises(generate.ImageGenError):
                generate.ensure_new_task(output_dir, task_id)

    def test_background_hide_is_scheduled_only_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result-task.json"
            path.write_text("{}", encoding="utf-8")
            with patch.object(generate.subprocess, "Popen") as popen:
                scheduled = generate._schedule_result_hide(path)
            self.assertEqual(scheduled, sys.platform.startswith("win"))
            if sys.platform.startswith("win"):
                popen.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows Explorer attribute test")
    def test_windows_result_json_can_be_hidden_without_renaming(self) -> None:
        import ctypes

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result-task-windows-0001.json"
            path.write_text("{}", encoding="utf-8")
            self.assertTrue(hide_result.hide_file(path))
            self.assertTrue(path.is_file())
            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            self.assertTrue(attributes & hide_result.FILE_ATTRIBUTE_HIDDEN)
            ctypes.windll.kernel32.SetFileAttributesW(
                str(path), attributes & ~hide_result.FILE_ATTRIBUTE_HIDDEN
            )


class SkillRoutingContractTests(unittest.TestCase):
    def test_skill_requires_exact_prior_result_for_edit_and_no_scan(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "Matrixapi-imagegen"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("reuse only the exact prior", text)
        self.assertIn("Never scan an output", text)
        self.assertIn("asks to generate a new/different image", text)
        self.assertIn("omit `--image` and `--reference-url`", text)
        self.assertIn("completed_at_ms", text)
        self.assertIn("do not open or scan the output directory", text)

    def test_new_generation_does_not_enter_edit_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            output = output_dir / "new.png"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "generate.py",
                        "--task-id",
                        "task-new-generation-0001",
                        "--prompt",
                        "a new image",
                        "--out-dir",
                        str(output_dir),
                    ],
                ),
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "key", "gpt-image-2", "test"),
                ),
                patch.object(generate, "call_api", return_value={"data": [{"url": "https://example/image"}]}) as call_generate,
                patch.object(generate, "call_edit_api") as call_edit,
                patch.object(generate, "save_images", return_value=[str(output)]),
                patch.object(generate, "emit_success", return_value={"ok": True}),
            ):
                self.assertEqual(generate.main(), 0)
            call_generate.assert_called_once()
            call_edit.assert_not_called()

    def test_edit_uses_only_the_explicit_prior_result_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            prior = output_dir / "prior.png"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(prior)
            output = output_dir / "edited.png"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "generate.py",
                        "--task-id",
                        "task-edit-prior-0001",
                        "--prompt",
                        "edit the prior image",
                        "--image",
                        str(prior),
                        "--out-dir",
                        str(output_dir),
                    ],
                ),
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "key", "gpt-image-2", "test"),
                ),
                patch.object(generate, "call_api") as call_generate,
                patch.object(generate, "call_edit_api", return_value={"data": [{"url": "https://example/image"}]}) as call_edit,
                patch.object(generate, "save_images", return_value=[str(output)]),
                patch.object(generate, "emit_success", return_value={"ok": True}),
            ):
                self.assertEqual(generate.main(), 0)
            call_generate.assert_not_called()
            call_edit.assert_called_once()
            self.assertEqual(call_edit.call_args.args[6], [str(prior)])


if __name__ == "__main__":
    unittest.main()
