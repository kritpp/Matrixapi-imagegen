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
    def test_long_prompt_is_sent_verbatim_when_upstream_accepts_it(self) -> None:
        prompt = "完整约束。" * 700
        bodies: list[dict] = []

        def fake_post(_endpoint, _key, body, _content_type, _timeout, _idempotency):
            bodies.append(generate.json.loads(body.decode("utf-8")))
            return {"data": [{"b64_json": "aW1hZ2U="}]}

        with mock.patch.object(generate, "_post_image_request", side_effect=fake_post):
            result = generate.call_api(
                "https://relay.test/v1/images/generations",
                "secret",
                "gpt-image-2",
                prompt,
                "4K",
                1,
                30,
            )
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["prompt"], prompt)
        self.assertNotIn("_matrixapi_prompt_compacted", result)

    def test_explicit_prompt_length_rejection_compacts_once(self) -> None:
        prompt = "主体与构图必须保留。不要遗漏角色。" * 200
        bodies: list[dict] = []

        def fake_post(_endpoint, _key, body, _content_type, _timeout, _idempotency):
            bodies.append(generate.json.loads(body.decode("utf-8")))
            if len(bodies) == 1:
                raise generate.ImageGenError(
                    "HTTP 400 prompt too long; maximum 1024 characters",
                    status_code=400,
                )
            return {"data": [{"b64_json": "aW1hZ2U="}]}

        with mock.patch.object(generate, "_post_image_request", side_effect=fake_post):
            result = generate.call_api(
                "https://relay.test/v1/images/generations",
                "secret",
                "gpt-image-2",
                prompt,
                "4K",
                1,
                30,
                idempotency_key="f" * 64,
            )
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0]["prompt"], prompt)
        self.assertLessEqual(len(bodies[1]["prompt"]), 1024)
        self.assertTrue(result["_matrixapi_prompt_compacted"])

    def test_generic_400_never_triggers_prompt_retry(self) -> None:
        with mock.patch.object(
            generate,
            "_post_image_request",
            side_effect=generate.ImageGenError("HTTP 400 request failed", status_code=400),
        ) as post:
            with self.assertRaises(generate.ImageGenError):
                generate.call_api(
                    "https://relay.test/v1/images/generations",
                    "secret",
                    "gpt-image-2",
                    "长提示词" * 1000,
                    "4K",
                    1,
                    30,
                )
        self.assertEqual(post.call_count, 1)

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

    def test_terminal_content_policy_failure_does_not_block_later_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "p" * 64
            record, lock, cached = generate.claim_idempotency(
                output_dir, fingerprint, "task-policy-first", 1
            )
            self.assertIsNone(cached)
            generate.finish_idempotency(
                record,
                lock,
                fingerprint,
                "task-policy-first",
                error="模型明确拒绝了这次内容/版权/安全策略请求",
                uncertain=False,
            )
            _record2, _lock2, cached2 = generate.claim_idempotency(
                output_dir, fingerprint, "task-policy-second", 1
            )
            self.assertIsNone(cached2)

    def test_old_idempotency_ledger_is_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "o" * 64
            record_path = generate.idempotency_record_path(output_dir, fingerprint)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                '{"version":1,"status":"uncertain","task_id":"old-task",'
                '"created_at_ms":9999999999999,"error":"HTTP 503"}',
                encoding="utf-8",
            )
            _record, _lock, cached = generate.claim_idempotency(
                output_dir, fingerprint, "task-new-1234", 1
            )
            self.assertIsNone(cached)


if __name__ == "__main__":
    unittest.main()
