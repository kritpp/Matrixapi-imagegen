from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "Matrixapi-imagegen"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import generate  # noqa: E402


class ImageRequestIdempotencyTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["generate.py", *argv]), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = generate.main()
        text = stdout.getvalue().strip() or stderr.getvalue().strip()
        return code, json.loads(text)

    def test_same_thread_same_request_reuses_result_without_second_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            image = output_dir / "generated.png"
            image.write_bytes(b"generated-image")
            common_args = [
                "--prompt",
                "a precise test image",
                "--size",
                "2K",
                "--quality",
                "high",
                "--out-dir",
                str(output_dir),
            ]
            with (
                patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-idempotency-1"}),
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=("https://matrixapii.com", "key", "gpt-image-2", "test"),
                ),
                patch.object(
                    generate,
                    "call_api",
                    return_value={"data": [{"url": "https://example/image"}]},
                ) as call_api,
                patch.object(generate, "save_images", return_value=[str(image)]),
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                first_code, first = self._run(
                    ["--task-id", "task-idempotency-first-0001", *common_args]
                )
                second_code, second = self._run(
                    ["--task-id", "task-idempotency-second-0001", *common_args]
                )

            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertEqual(call_api.call_count, 1)
            self.assertNotIn("idempotency_reused", first)
            self.assertTrue(second["idempotency_reused"])
            self.assertEqual(
                second["reused_from_task_id"], "task-idempotency-first-0001"
            )
            self.assertEqual(second["preview_files"], first["preview_files"])
            self.assertEqual(second["task_id"], "task-idempotency-second-0001")
            self.assertGreaterEqual(
                second["completed_at_ms"], second["request_started_at_ms"]
            )

    def test_failed_identical_request_is_not_submitted_automatically_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            common_args = [
                "--prompt",
                "a request that fails after submission",
                "--out-dir",
                str(output_dir),
            ]
            with (
                patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-idempotency-2"}),
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=("https://matrixapii.com", "key", "gpt-image-2", "test"),
                ),
                patch.object(
                    generate,
                    "call_api",
                    side_effect=generate.ImageGenError("upstream timed out after billing"),
                ) as call_api,
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                first_code, first = self._run(
                    ["--task-id", "task-idempotency-failed-0001", *common_args]
                )
                second_code, second = self._run(
                    ["--task-id", "task-idempotency-failed-0002", *common_args]
                )

            self.assertEqual(first_code, 1)
            self.assertEqual(second_code, 1)
            self.assertEqual(call_api.call_count, 1)
            self.assertIn("upstream timed out", first["error"])
            self.assertIn("will not be submitted again automatically", second["error"])

    def test_safe_502_submission_is_retried_twice_with_one_idempotency_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            image = output_dir / "generated.png"
            image.write_bytes(b"generated-image")
            transient = generate.UpstreamSubmitError(
                "HTTP 502 [upstream_service] temporary",
                safe_to_retry=True,
                status_code=502,
            )
            with (
                patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-retry-502"}),
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=("https://matrixapii.com", "key", "gpt-image-2", "test"),
                ),
                patch.object(
                    generate,
                    "call_api",
                    side_effect=[transient, transient, {"data": [{"url": "https://example/image"}]}],
                ) as call_api,
                patch.object(generate, "save_images", return_value=[str(image)]),
                patch.object(generate.time, "sleep", return_value=None),
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                code, result = self._run(
                    [
                        "--task-id",
                        "task-safe-retry-502-0001",
                        "--prompt",
                        "safe transient retry",
                        "--out-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(call_api.call_count, 3)
            self.assertEqual(result["submit_attempts"], 3)
            keys = [call.kwargs["idempotency_key"] for call in call_api.call_args_list]
            self.assertEqual(len(set(keys)), 1)

    def test_safe_transient_failure_stops_after_three_attempts_and_locks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            failure = generate.UpstreamSubmitError(
                "HTTP 503 [upstream_service] temporary",
                safe_to_retry=True,
                status_code=503,
            )
            common = ["--prompt", "three attempts only", "--out-dir", str(output_dir)]
            with (
                patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-retry-limit"}),
                patch.object(
                    generate,
                    "discover_credentials",
                    return_value=("https://matrixapii.com", "key", "gpt-image-2", "test"),
                ),
                patch.object(generate, "call_api", side_effect=failure) as call_api,
                patch.object(generate.time, "sleep", return_value=None),
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                first_code, _ = self._run(
                    ["--task-id", "task-retry-limit-first", *common]
                )
                second_code, second = self._run(
                    ["--task-id", "task-retry-limit-second", *common]
                )

            self.assertEqual(first_code, 1)
            self.assertEqual(second_code, 1)
            self.assertEqual(call_api.call_count, 3)
            self.assertIn("will not be submitted again automatically", second["error"])

    def test_all_supported_gateway_statuses_are_retryable(self) -> None:
        for status in (502, 503, 504):
            with self.subTest(status=status), patch.object(
                generate.time, "sleep", return_value=None
            ):
                submit = Mock(
                    side_effect=[
                        generate.UpstreamSubmitError(
                            f"HTTP {status}", safe_to_retry=True, status_code=status
                        ),
                        {"data": [{"url": "https://example/image"}]},
                    ]
                )
                result, attempts = generate.submit_with_safe_retries(submit)
                self.assertTrue(result["data"])
                self.assertEqual(attempts, 2)
                self.assertEqual(submit.call_count, 2)

    def test_timeout_or_existing_async_task_is_never_resubmitted(self) -> None:
        unsafe = Mock(
            side_effect=generate.UpstreamSubmitError(
                "request timeout; acceptance unknown", safe_to_retry=False
            )
        )
        with self.assertRaises(generate.UpstreamSubmitError):
            generate.submit_with_safe_retries(unsafe)
        self.assertEqual(unsafe.call_count, 1)

        accepted = Mock(return_value={"task_id": "task-upstream-accepted"})
        result, attempts = generate.submit_with_safe_retries(accepted)
        self.assertEqual(result["task_id"], "task-upstream-accepted")
        self.assertEqual(attempts, 1)
        self.assertEqual(accepted.call_count, 1)

    def test_force_new_and_different_threads_do_not_reuse(self) -> None:
        request = {"mode": "generate", "prompt": "same", "model": "gpt-image-2"}
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-a"}):
            first = generate.request_fingerprint("task-aaaaaaaa", request)
            forced = generate.request_fingerprint(
                "task-bbbbbbbb", request, force_new=True
            )
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-b"}):
            other_thread = generate.request_fingerprint("task-cccccccc", request)
        self.assertNotEqual(first, forced)
        self.assertNotEqual(first, other_thread)

    def test_reference_content_and_order_are_part_of_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "first.png"
            second_path = Path(temp_dir) / "second.png"
            Image.new("RGB", (8, 8), "red").save(first_path)
            Image.new("RGB", (8, 8), "blue").save(second_path)
            _, _, first_order = generate._prepare_upload_files(
                [str(first_path), str(second_path)]
            )
            _, _, second_order = generate._prepare_upload_files(
                [str(second_path), str(first_path)]
            )
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-reference-order"}):
                first = generate.request_fingerprint(
                    "task-order-0001", {"local_references": first_order}
                )
                second = generate.request_fingerprint(
                    "task-order-0002", {"local_references": second_order}
                )
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
