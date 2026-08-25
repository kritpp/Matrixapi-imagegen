import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "generate.py"
SPEC = importlib.util.spec_from_file_location("matrixapi_imagegen", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ImageMetadataTests(unittest.TestCase):
    def test_edit_size_preserves_requested_1k_2k_and_4k(self):
        for size in ("1024x1024", "2048x2048", "3840x2160"):
            self.assertEqual(MODULE.edit_working_size(size), size)

    def test_png_4k_metadata(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (3840).to_bytes(4, "big") + (2160).to_bytes(4, "big")
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 3840, "height": 2160, "format": "PNG", "resolution": "4K"},
        )

    def test_gif_2k_metadata(self):
        data = b"GIF89a" + (2048).to_bytes(2, "little") + (1152).to_bytes(2, "little")
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 2048, "height": 1152, "format": "GIF", "resolution": "2K"},
        )

    def test_webp_1k_metadata(self):
        data = b"RIFF" + b"\x00\x00\x00\x00WEBPVP8X" + b"\x0a\x00\x00\x00" + b"\x00\x00\x00\x00" + (1023).to_bytes(3, "little") + (767).to_bytes(3, "little")
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 1024, "height": 768, "format": "WEBP", "resolution": "1K"},
        )

    def test_jpeg_2k_metadata(self):
        data = (
            b"\xff\xd8\xff\xc0\x00\x11\x08"
            + (2048).to_bytes(2, "big")
            + (2048).to_bytes(2, "big")
            + b"\x00" * 10
        )
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 2048, "height": 2048, "format": "JPEG", "resolution": "2K"},
        )

    def test_save_images_reports_saved_image_metadata(self):
        data = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + (3840).to_bytes(4, "big")
            + (2160).to_bytes(4, "big")
        )
        encoded_image = base64.b64encode(data).decode("ascii")
        result = {"data": [{"b64_json": encoded_image}]}
        with TemporaryDirectory() as output_dir:
            paths, image_info = MODULE.save_images(
                result, "", "", Path(output_dir), 1
            )

            self.assertEqual(len(paths), 1)
            self.assertTrue(Path(paths[0]).is_file())
            self.assertEqual(Path(paths[0]).read_bytes(), data)
            self.assertEqual(
                image_info,
                [{"width": 3840, "height": 2160, "format": "PNG", "resolution": "4K"}],
            )

    def test_old_ready_result_never_blocks_a_new_execution(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            old_image = output_path / "image-old.png"
            old_image.write_bytes(b"old image")
            MODULE._write_sidecar(
                MODULE._ready_marker(output_path, "same-task"),
                {
                    "ok": True,
                    "request_id": "same-task",
                    "execution_id": "old-execution",
                    "preview_files": [old_image.resolve().as_posix()],
                },
            )
            new_image = output_path / "image-new.png"
            new_image.write_bytes(b"new image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "same-task",
                "--prompt",
                "a cat",
                "--out-dir",
                str(output_path),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}) as call_api,
                mock.patch.object(
                    MODULE,
                    "save_images",
                    return_value=([str(new_image)], [{"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}]),
                ),
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            call_api.assert_called_once()
            self.assertEqual(json.loads(stdout.getvalue())["preview_files"], [new_image.resolve().as_posix()])

    def test_overlapping_request_reuses_first_result(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_path = output_path / "image-current.png"
            image_path.write_bytes(b"validated image")
            first_running, first_execution, existing = MODULE._reserve_request(
                output_path, "same-running-task", False, 10
            )
            self.assertIsNotNone(first_running)
            self.assertIsNone(existing)
            payload = {
                "ok": True,
                "request_id": "same-running-task",
                "execution_id": first_execution,
                "preview_files": [image_path.resolve().as_posix()],
            }

            def finish_first_request(_seconds):
                MODULE._write_sidecar(
                    MODULE._ready_marker(output_path, "same-running-task"), payload
                )
                MODULE._release_request(first_running)

            with mock.patch.object(MODULE.time, "sleep", side_effect=finish_first_request):
                second_running, second_execution, reused = MODULE._reserve_request(
                    output_path, "same-running-task", True, 10
                )

            self.assertIsNone(second_running)
            self.assertEqual(second_execution, first_execution)
            self.assertEqual(reused, payload)

    def test_same_request_id_after_completed_run_calls_image_api_again(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_path = output_path / "image-current.png"
            image_path.write_bytes(b"validated image")
            payload = {
                "ok": True,
                "request_id": "existing-ready-task",
                "execution_id": "finished-execution",
                "preview_files": [image_path.resolve().as_posix()],
            }
            MODULE._write_sidecar(
                MODULE._ready_marker(output_path, "existing-ready-task"), payload
            )
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "existing-ready-task",
                "--prompt",
                "a cat",
                "--out-dir",
                str(output_path),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api") as call_api,
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            call_api.assert_called_once()
            self.assertNotEqual(
                json.loads(stdout.getvalue())["execution_id"], "finished-execution"
            )

    def test_config_check_reports_exact_skill_version(self):
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["generate.py", "--check-config"]),
            mock.patch.object(
                MODULE,
                "discover_credentials",
                return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
            ),
            mock.patch.object(sys, "stdout", stdout),
        ):
            self.assertEqual(MODULE.main(), 0)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["skill_version"], "1.8.13")
        self.assertEqual(payload["supported_models"], ["gpt-image-2", "gpt-image-2-pro"])

    def test_portrait_defaults_to_comic_friendly_two_by_three(self):
        self.assertEqual(MODULE.default_aspect_ratio("2160x3840"), "2:3")
        self.assertEqual(MODULE.default_aspect_ratio("2160x3840", "9:16"), "9:16")
        self.assertEqual(MODULE.default_aspect_ratio("3840x2160"), "")

    def test_graphic_injury_language_is_rewritten_once_before_api(self):
        rewritten, changed = MODULE.sanitize_prompt("a bloody violent fight with a severed limb")
        self.assertTrue(changed)
        self.assertNotIn("bloody", rewritten.lower())
        self.assertNotIn("violent", rewritten.lower())
        self.assertNotIn("severed", rewritten.lower())
        self.assertIn("cinematic", rewritten.lower())

    def test_safe_prompt_is_unchanged(self):
        prompt = "a calm portrait in soft daylight"
        self.assertEqual(MODULE.sanitize_prompt(prompt), (prompt, False))

    def test_five_requested_outputs_run_sequentially_without_a_skill_cap(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_paths = [output_path / f"image-{index}.png" for index in range(5)]
            for image_path in image_paths:
                image_path.write_bytes(b"validated image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "five-output-task",
                "--prompt",
                "five poster variants",
                "--n",
                "5",
                "--out-dir",
                str(output_path),
            ]
            image_info = [
                {"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}
                for _ in image_paths
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}) as call_api,
                mock.patch.object(
                    MODULE,
                    "save_images",
                    side_effect=[([str(path)], [info]) for path, info in zip(image_paths, image_info)],
                ),
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            self.assertEqual(call_api.call_count, 5)
            self.assertTrue(all(call.args[5] == 1 for call in call_api.call_args_list))
            events = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual([event["event"] for event in events[:-1]], ["image_saved"] * 5)
            self.assertEqual(events[-1]["event"], "complete")
            self.assertEqual(events[-1]["requested_count"], 5)
            self.assertEqual(events[-1]["count"], 5)
            self.assertFalse(events[-1]["partial"])

    def test_three_page_story_uses_one_unified_prompt_and_returns_each_page(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_paths = [output_path / f"page-{index}.png" for index in range(1, 4)]
            for image_path in image_paths:
                image_path.write_bytes(b"validated image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "three-page-story-task",
                "--prompt",
                "Create three continuous portrait comic pages: page one begins the confrontation, page two advances the same confrontation, page three resolves it; same characters, costume, visual language, and chronology across all pages.",
                "--n",
                "3",
                "--out-dir",
                str(output_path),
            ]
            image_info = {
                "width": 1024,
                "height": 1536,
                "format": "PNG",
                "resolution": "1K",
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}) as call_api,
                mock.patch.object(
                    MODULE,
                    "save_images",
                    side_effect=[([str(path)], [image_info]) for path in image_paths],
                ),
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            self.assertEqual(call_api.call_count, 3)
            self.assertTrue(all(call.args[3] == call_api.call_args_list[0].args[3] for call in call_api.call_args_list))
            self.assertTrue(all(call.args[5] == 1 for call in call_api.call_args_list))
            events = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual([event["image_index"] for event in events[:-1]], [1, 2, 3])
            self.assertEqual(events[-1]["requested_count"], 3)
            self.assertEqual(events[-1]["count"], 3)

    def test_later_output_failure_keeps_earlier_saved_image_without_retry(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            first_image = output_path / "image-first.png"
            first_image.write_bytes(b"validated image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "partial-output-task",
                "--prompt",
                "three poster variants",
                "--n",
                "3",
                "--out-dir",
                str(output_path),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2-pro", "test"),
                ),
                mock.patch.object(
                    MODULE,
                    "call_api",
                    side_effect=[{"data": [{}]}, MODULE.ImageGenError("second output failed")],
                ) as call_api,
                mock.patch.object(
                    MODULE,
                    "save_images",
                    return_value=(
                        [str(first_image)],
                        [{"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}],
                    ),
                ),
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            self.assertEqual(call_api.call_count, 2)
            events = [json.loads(line) for line in stdout.getvalue().splitlines()]
            final = events[-1]
            self.assertTrue(final["ok"])
            self.assertTrue(final["partial"])
            self.assertFalse(final["complete"])
            self.assertEqual(final["count"], 1)
            self.assertEqual(final["requested_count"], 3)
            self.assertEqual(final["failed_output"], 2)
            self.assertEqual(final["error"], "second output failed")
            self.assertEqual(final["preview_files"], [first_image.resolve().as_posix()])

    def test_default_output_directory_uses_current_skill_name(self):
        with TemporaryDirectory() as home_dir:
            output_dir = Path(home_dir) / ".codex" / "generated_images" / "Matrixapi-imagegen"
            image_path = output_dir / "generated.png"
            stdout = io.StringIO()
            argv = ["generate.py", "--request-id", "new-output-dir", "--prompt", "a cat"]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(Path, "home", return_value=Path(home_dir)),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}),
                mock.patch.object(
                    MODULE,
                    "save_images",
                    return_value=(
                        [str(image_path)],
                        [{"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}],
                    ),
                ) as save_images,
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            self.assertEqual(save_images.call_args.args[3], output_dir)

    def test_default_timeout_is_six_hundred_seconds(self):
        with mock.patch.object(sys, "argv", ["generate.py", "--prompt", "a cat"]):
            self.assertEqual(MODULE.parse_args().timeout, 600)

    def test_zero_requested_outputs_are_rejected_before_api_call(self):
        stderr = io.StringIO()
        argv = [
            "generate.py",
            "--request-id",
            "zero-output-task",
            "--prompt",
            "no output",
            "--n",
            "0",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(MODULE, "discover_credentials") as discover_credentials,
            mock.patch.object(MODULE, "call_api") as call_api,
            mock.patch.object(sys, "stderr", stderr),
        ):
            self.assertEqual(MODULE.main(), 1)

        discover_credentials.assert_not_called()
        call_api.assert_not_called()
        self.assertEqual(json.loads(stderr.getvalue())["error"], "Image count must be at least 1")

    def test_high_quality_is_sent_once_and_reported(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_path = output_path / "image-high.png"
            image_path.write_bytes(b"validated image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "high-quality-task",
                "--prompt",
                "a detailed poster",
                "--quality",
                "high",
                "--out-dir",
                str(output_path),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}) as call_api,
                mock.patch.object(
                    MODULE,
                    "save_images",
                    return_value=([str(image_path)], [{"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}]),
                ),
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            call_api.assert_called_once()
            self.assertEqual(call_api.call_args.args[-1], {"quality": "high"})
            self.assertEqual(json.loads(stdout.getvalue())["quality"], "high")

    def test_accepts_sixteen_reference_images(self):
        image_paths = [f"reference-{index}.png" for index in range(16)]
        with mock.patch.object(MODULE, "_input_image", return_value=("reference.png", "image/png", b"image")):
            with mock.patch.object(MODULE, "_post_image_request", return_value={"data": [{}]}) as post_request:
                MODULE.call_edit_api(
                    "https://eos.manyuvip.com/v1/images/edits",
                    "test-key",
                    "gpt-image-2",
                    "combine references",
                    "1024x1024",
                    1,
                    image_paths,
                    None,
                    10,
                )

        post_request.assert_called_once()

    def test_rejects_incomplete_current_message_attachments_before_api_call(self):
        argv = [
            "generate.py",
            "--request-id",
            "missing-attachments",
            "--prompt",
            "combine all current references",
            "--expected-images",
            "16",
        ]
        for index in range(11):
            argv.extend(("--image", f"reference-{index}.png"))

        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                MODULE,
                "discover_credentials",
                return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
            ),
            mock.patch.object(MODULE, "call_edit_api") as call_edit_api,
            mock.patch.object(sys, "stderr", stderr),
        ):
            self.assertEqual(MODULE.main(), 1)

        call_edit_api.assert_not_called()
        error = json.loads(stderr.getvalue())["error"]
        self.assertIn("expected 16, received 11, missing 5", error)
        self.assertIn("no image request was sent", error)

    def test_expected_image_count_does_not_affect_text_generation(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_path = output_path / "generated.png"
            image_path.write_bytes(b"validated image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "text-only-generation",
                "--prompt",
                "a new image without references",
                "--out-dir",
                str(output_path),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}) as call_api,
                mock.patch.object(
                    MODULE,
                    "save_images",
                    return_value=([str(image_path)], [{"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}]),
                ),
                mock.patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(MODULE.main(), 0)

            call_api.assert_called_once()
            self.assertEqual(json.loads(stdout.getvalue())["mode"], "generate")

    def test_success_publishes_one_ready_sidecar_without_cleanup_markers(self):
        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            image_path = output_path / "image-current.png"
            image_path.write_bytes(b"validated image")
            stdout = io.StringIO()
            argv = [
                "generate.py",
                "--request-id",
                "ready-task",
                "--prompt",
                "a cat",
                "--out-dir",
                str(output_path),
            ]

            def assert_success_precedes_hiding(path):
                if path.name.startswith(".ready-"):
                    self.assertTrue(json.loads(stdout.getvalue())["ok"])

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    MODULE,
                    "discover_credentials",
                    return_value=("https://eos.manyuvip.com", "test-key", "gpt-image-2", "test"),
                ),
                mock.patch.object(MODULE, "call_api", return_value={"data": [{}]}),
                mock.patch.object(
                    MODULE,
                    "save_images",
                    return_value=([str(image_path)], [{"width": 1024, "height": 1024, "format": "PNG", "resolution": "1K"}]),
                ),
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(
                    MODULE, "_hide_sidecar", side_effect=assert_success_precedes_hiding
                ),
            ):
                self.assertEqual(MODULE.main(), 0)

            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["execution_id"])
            self.assertEqual(
                json.loads((output_path / ".ready-ready-task.json").read_text(encoding="utf-8")),
                payload,
            )
            self.assertFalse((output_path / ".result-ready-task.json").exists())
            self.assertFalse((output_path / ".completed-ready-task.json").exists())
            self.assertFalse((output_path / ".running-ready-task.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows Explorer attributes are platform-specific")
    def test_sidecar_is_hidden_without_changing_its_path(self):
        with TemporaryDirectory() as output_dir:
            sidecar = Path(output_dir) / ".result-task.json"
            sidecar.write_text("{}", encoding="utf-8")
            MODULE._hide_sidecar(sidecar)
            self.assertTrue(sidecar.is_file())
            import ctypes

            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(sidecar))
            self.assertNotEqual(attributes, 0xFFFFFFFF)
            self.assertTrue(attributes & 0x2)


if __name__ == "__main__":
    unittest.main()
