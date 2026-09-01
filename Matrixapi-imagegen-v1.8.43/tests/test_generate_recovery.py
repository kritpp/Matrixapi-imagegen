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
    def test_8k_size_is_preserved_without_local_downscale(self) -> None:
        self.assertEqual(generate.normalize_size("8K"), "8K")
        self.assertEqual(
            generate.legacy_pixel_size("8K", "16:9"), "7680x4320"
        )
        self.assertEqual(generate.normalize_size("7680x4320"), "7680x4320")

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

    def test_ordinary_1k_task_envelope_is_polled_without_a_second_submit(self) -> None:
        acknowledged = {"id": "task-1k-202", "status": "queued"}
        completed = {"data": [{"url": "https://example.test/1k.png"}]}

        with mock.patch.object(
            generate, "wait_for_task", return_value=completed
        ) as status_wait:
            result = generate.resolve_image_task_response(
                acknowledged,
                "https://relay.test/v1/images/generations",
                "secret",
                30,
                async_mode=False,
            )

        self.assertEqual(result, completed)
        status_wait.assert_called_once_with(
            acknowledged,
            "https://relay.test/v1/images/generations",
            "secret",
            30,
        )

    def test_direct_image_response_does_not_poll(self) -> None:
        direct = {"data": [{"b64_json": "aW1hZ2U="}]}
        with mock.patch.object(generate, "wait_for_task") as status_wait:
            result = generate.resolve_image_task_response(
                direct,
                "https://relay.test/v1/images/generations",
                "secret",
                30,
                async_mode=False,
            )
        self.assertEqual(result, direct)
        status_wait.assert_not_called()

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

    def test_interrupted_task_claim_returns_recovery_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "r" * 64
            record_path = generate.idempotency_record_path(output_dir, fingerprint)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                '{"version":3,"status":"in_progress",'
                '"task_id":"task-original-1234","created_at_ms":'
                + str(generate.time.time_ns() // 1_000_000)
                + ',"request_started_at_ms":1}',
                encoding="utf-8",
            )
            lock_path = record_path.with_suffix(".lock")
            lock_path.write_text("999999999", encoding="ascii")
            _record, _lock, recovery = generate.claim_idempotency(
                output_dir, fingerprint, "task-new-5678", 1
            )
            self.assertIsNotNone(recovery)
            self.assertTrue(recovery.get("_recovery_record"))
            self.assertEqual(recovery.get("task_id"), "task-original-1234")

    def test_task_result_lookup_never_uses_another_task_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            old_dir = output_dir / "task-old-1234"
            current_dir = output_dir / "task-current-1234"
            old_dir.mkdir()
            current_dir.mkdir()
            (old_dir / "image-old.png").write_bytes(b"old")
            (current_dir / "image-current.png").write_bytes(b"current")
            self.assertEqual(
                generate._task_result_files(output_dir, "task-current-1234"),
                [str((current_dir / "image-current.png").resolve().as_posix())],
            )

    def test_save_images_publishes_under_shared_image_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            files = generate.save_images(
                {"data": [{"b64_json": "iVBORw0KGgo="}]},
                "https://relay.test/v1/images/generations",
                "secret",
                output_dir,
                30,
                task_id="task-scoped-1234",
            )
            self.assertEqual(1, len(files))
            self.assertEqual(Path(files[0]).parent, output_dir.resolve())
            self.assertTrue(Path(files[0]).name.startswith("image-task-scoped-1234-"))
            self.assertFalse((output_dir / "task-scoped-1234").exists())
            self.assertFalse((output_dir / ".staging").exists())

    def test_empty_image_response_does_not_create_task_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with self.assertRaises(generate.ImageGenError):
                generate.save_images(
                    {"data": []},
                    "https://relay.test/v1/images/generations",
                    "secret",
                    output_dir,
                    30,
                    task_id="task-empty-1234",
                )
            self.assertFalse((output_dir / "task-empty-1234").exists())
            self.assertFalse((output_dir / ".staging").exists())

    def test_recovery_uses_existing_local_image_without_status_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-recover-1234"
            fingerprint = "z" * 64
            task_dir = output_dir / task_id
            task_dir.mkdir()
            image = task_dir / "image-current.png"
            image.write_bytes(b"current-image")
            record_path = generate.idempotency_record_path(output_dir, fingerprint)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                generate.json.dumps(
                    {
                        "version": generate.IDEMPOTENCY_VERSION,
                        "status": "in_progress",
                        "task_id": task_id,
                        "request_started_at_ms": 1,
                        "request_context": {
                            "mode": "edit",
                            "model": "gpt-image-2",
                            "size": "4K",
                            "requested_size": "4K",
                            "quality": "high",
                            "aspect_ratio": "16:9",
                        },
                    }
                ),
                encoding="utf-8",
            )
            lock_path = record_path.with_suffix(".lock")
            with mock.patch.object(generate, "_schedule_result_hide", return_value=False), mock.patch.object(
                generate, "_get_image_request"
            ) as status_get:
                result = generate.recover_submitted_task(
                    generate._load_idempotency_record(record_path) or {},
                    record_path,
                    lock_path,
                    fingerprint,
                    output_dir,
                    "https://relay.test/v1/images/generations",
                    "secret",
                    30,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["recovered_after_reconnect"])
            self.assertEqual(result["preview_files"], [image.resolve().as_posix()])
            status_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
