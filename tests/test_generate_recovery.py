from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "Matrixapi-imagegen"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("matrixapi_generate", SCRIPT_DIR / "generate.py")
generate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(generate)


class AsyncResultRecoveryTests(unittest.TestCase):
    def test_common_success_wrappers_are_normalized(self) -> None:
        cases = (
            {"status": "success", "image": "https://example.test/a.png"},
            {
                "state": "completed",
                "result": {"images": [{"url": "https://example.test/a.png"}]},
            },
            {
                "status": "succeeded",
                "output": {"data": [{"url": "https://example.test/a.png"}]},
            },
            {"status": "done", "files": ["https://example.test/a.png"]},
        )
        for value in cases:
            with self.subTest(value=value):
                result = generate._normalize_task_result(value)
                self.assertEqual(result["data"][0]["url"], "https://example.test/a.png")

    def test_same_reference_content_has_same_request_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "clipboard-a.png"
            second = Path(directory) / "clipboard-b.png"
            content = b"same-reference-content"
            first.write_bytes(content)
            second.write_bytes(content)
            one = {"prompt": "same", "local_references": generate._local_reference_fingerprint([str(first)], None)}
            two = {"prompt": "same", "local_references": generate._local_reference_fingerprint([str(second)], None)}
            self.assertEqual(
                generate.request_fingerprint("task-11111111", one),
                generate.request_fingerprint("task-22222222", two),
            )

    def test_transient_status_error_retries_get_without_resubmitting(self) -> None:
        responses = iter(
            (
                generate.ImageGenError("HTTP 503 temporary"),
                {
                    "status": "success",
                    "result": {"images": [{"url": "https://example.test/a.png"}]},
                },
            )
        )

        def fake_get(*_args):
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(generate, "_get_image_request", side_effect=fake_get) as status_get:
            with mock.patch.object(generate.time, "sleep"):
                result = generate.wait_for_task(
                    {"task_id": "task-12345678"},
                    "https://relay.test/v1/images/generations",
                    "secret",
                    10,
                )
        self.assertEqual(result["data"][0]["url"], "https://example.test/a.png")
        self.assertEqual(status_get.call_count, 2)

    def test_transient_submit_failure_is_not_replayed_as_stale_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "f" * 64
            record, lock, cached = generate.claim_idempotency(
                output_dir, fingerprint, "task-first-1234", 1
            )
            self.assertIsNone(cached)
            error = generate.ImageGenError(
                "HTTP 503 [upstream_service]", status_code=503, retryable=True
            )
            generate.finish_idempotency(
                record,
                lock,
                fingerprint,
                "task-first-1234",
                error=str(error),
                uncertain=False,
            )
            # The next request gets a fresh claim; it never receives the old
            # 503 payload as an idempotency replay.
            _record2, _lock2, cached2 = generate.claim_idempotency(
                output_dir, fingerprint, "task-second-1234", 1
            )
            self.assertIsNone(cached2)


if __name__ == "__main__":
    unittest.main()
