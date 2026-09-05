from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
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


def png_bytes(width: int = 1672, height: int = 941) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


def task_png_path(
    output_dir: Path, task_id: str, fingerprint: str, index: int = 1
) -> Path:
    return output_dir / (
        f"{generate._task_image_prefix(task_id, fingerprint)}"
        f"20260905-120000-deadbeef-{index}.png"
    )


class AsyncResultRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result_hide = mock.patch.object(
            generate, "_schedule_result_hide", return_value=False
        )
        self.result_hide.start()

    def tearDown(self) -> None:
        self.result_hide.stop()

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

    def test_same_reference_content_isolated_by_exact_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "clipboard-a.png"
            second = Path(directory) / "clipboard-b.png"
            content = b"same-reference-content"
            first.write_bytes(content)
            second.write_bytes(content)
            one = {"prompt": "same", "local_references": generate._local_reference_fingerprint([str(first)], None)}
            two = {"prompt": "same", "local_references": generate._local_reference_fingerprint([str(second)], None)}
            # A fresh task must never inherit an older task's image, even in
            # the same conversation with identical reference bytes.
            with mock.patch.dict(
                generate.os.environ,
                {"CODEX_THREAD_ID": "thread-test-1234", "CODEX_SESSION_ID": "session-test"},
                clear=True,
            ):
                self.assertNotEqual(
                    generate.request_fingerprint("task-11111111", one),
                    generate.request_fingerprint("task-22222222", two),
                )

    def test_new_tasks_are_isolated_when_thread_id_is_missing(self) -> None:
        request = {"prompt": "same", "model": "gpt-image-2"}
        # A desktop session can contain several conversations.  Without a
        # thread id, a new task must not inherit another task's result.
        with mock.patch.dict(
            generate.os.environ,
            {"CODEX_SESSION_ID": "shared-runtime-session"},
            clear=True,
        ):
            first = generate.request_fingerprint("task-11111111", request)
            second = generate.request_fingerprint("task-22222222", request)
        self.assertNotEqual(first, second)

    def test_same_task_remains_recoverable_without_thread_id(self) -> None:
        request = {"prompt": "same", "model": "gpt-image-2"}
        with mock.patch.dict(generate.os.environ, {}, clear=True):
            self.assertEqual(
                generate.request_fingerprint("task-11111111", request),
                generate.request_fingerprint("task-11111111", request),
            )

    def test_same_task_remains_recoverable_with_thread_id(self) -> None:
        request = {"prompt": "same", "model": "gpt-image-2"}
        with mock.patch.dict(
            generate.os.environ, {"CODEX_THREAD_ID": "thread-test-1234"}, clear=True
        ):
            self.assertEqual(
                generate.request_fingerprint("task-11111111", request),
                generate.request_fingerprint("task-11111111", request),
            )

    def test_missing_thread_cannot_reuse_another_task_success(self) -> None:
        request = {"prompt": "same", "model": "gpt-image-2"}
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            image = output_dir / "image-task-11111111.png"
            image.write_bytes(b"image")
            payload = {
                "ok": True,
                "task_id": "task-11111111",
                "preview_files": [image.resolve().as_posix()],
                "download_files": [image.resolve().as_posix()],
            }
            with mock.patch.dict(generate.os.environ, {}, clear=True), mock.patch.object(
                generate, "_schedule_result_hide", return_value=False
            ):
                first_fingerprint = generate.request_fingerprint("task-11111111", request)
                record, lock, cached = generate.claim_idempotency(
                    output_dir, first_fingerprint, "task-11111111", 1
                )
                self.assertIsNone(cached)
                generate.finish_idempotency(
                    record,
                    lock,
                    first_fingerprint,
                    "task-11111111",
                    payload=payload,
                )
                second_fingerprint = generate.request_fingerprint("task-22222222", request)
                record2, lock2, cached2 = generate.claim_idempotency(
                    output_dir, second_fingerprint, "task-22222222", 1
                )
                self.assertIsNone(cached2)
                lock2.unlink(missing_ok=True)

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

    def test_terminal_rate_limit_is_not_replayed_as_stale_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "f" * 64
            record, lock, cached = generate.claim_idempotency(
                output_dir, fingerprint, "task-first-1234", 1
            )
            self.assertIsNone(cached)
            error = generate.ImageGenError(
                "HTTP 429 [upstream_service]",
                status_code=429,
                retryable=True,
                known_terminal=True,
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
            # terminal 429 payload as an idempotency replay.
            _record2, _lock2, cached2 = generate.claim_idempotency(
                output_dir, fingerprint, "task-second-1234", 1
            )
            self.assertIsNone(cached2)

    def test_known_failure_removes_record_and_lock_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "k" * 64
            record, lock, _cached = generate.claim_idempotency(
                output_dir, fingerprint, "task-known-1234", 1
            )
            generate.finish_idempotency(
                record,
                lock,
                fingerprint,
                "task-known-1234",
                error="HTTP 400 request failed",
                uncertain=False,
            )
            self.assertFalse(record.exists())
            self.assertFalse(lock.exists())

    def test_uncertain_failure_keeps_recovery_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "u" * 64
            task_id = "task-unknown-1234"
            record, lock, _cached = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            generate.update_idempotency_request(
                record,
                task_id,
                output_dir,
                {"mode": "generate", "model": "gpt-image-2"},
            )
            generate.mark_idempotency_submission(
                record,
                task_id,
                {"task_id": "upstream-1234", "status": "queued"},
                "https://relay.test/v1/images/generations",
                {"mode": "generate", "model": "gpt-image-2"},
            )
            with mock.patch.object(generate, "_schedule_result_hide", return_value=False):
                generate.finish_idempotency(
                    record,
                    lock,
                    fingerprint,
                    task_id,
                    error="status connection was interrupted",
                    uncertain=True,
                )
            saved = generate._load_idempotency_record(record) or {}
            self.assertEqual(saved["status"], "uncertain")
            self.assertEqual(saved["upstream_task_id"], "upstream-1234")
            self.assertEqual(saved["request_context"]["model"], "gpt-image-2")
            self.assertFalse(lock.exists())

    def test_uncertain_write_failure_retains_process_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "w" * 64
            task_id = "task-unknown-write-1234"
            record, lock, _cached = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            with mock.patch.object(
                generate,
                "_atomic_write_json",
                side_effect=generate.ImageGenError("disk unavailable"),
            ):
                generate.finish_idempotency(
                    record,
                    lock,
                    fingerprint,
                    task_id,
                    error="request outcome unknown",
                    uncertain=True,
                )
            self.assertTrue(record.exists())
            self.assertTrue(lock.exists())
            lock.unlink(missing_ok=True)

    def test_terminal_success_cleans_json_but_keeps_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-clean-1234"
            fingerprint = "c" * 64
            image = output_dir / f"image-{task_id}-result.png"
            image.write_bytes(b"image")
            record, lock, _cached = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            payload = {
                "ok": True,
                "task_id": task_id,
                "preview_files": [image.resolve().as_posix()],
                "download_files": [image.resolve().as_posix()],
            }
            generate.finish_idempotency(
                record, lock, fingerprint, task_id, payload=payload
            )
            with mock.patch.object(
                generate,
                "_schedule_result_cleanup",
                side_effect=lambda paths: generate._remove_transient_files(paths) or True,
            ):
                with mock.patch("sys.stdout", io.StringIO()):
                    generate.emit_success(
                        payload,
                        output_dir,
                        task_id,
                        1,
                        cleanup_paths=[record],
                    )
            self.assertTrue(image.exists())
            self.assertFalse(record.exists())
            self.assertFalse(generate.result_record_path(output_dir, task_id).exists())

    def test_exact_completed_task_is_recovered_without_new_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-local-1234"
            fingerprint = generate.request_fingerprint(
                task_id, {"prompt": "same", "model": "gpt-image-2"}
            )
            image = task_png_path(output_dir, task_id, fingerprint)
            image.write_bytes(png_bytes())
            record, lock, cached = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            self.assertIsNotNone(cached)
            self.assertEqual(
                cached.get("_local_task_files"), [image.resolve().as_posix()]
            )
            self.assertFalse(record.exists())
            self.assertFalse(lock.exists())

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

    def test_old_uncertain_idempotency_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "o" * 64
            task_id = "task-old-v1-1234"
            record_path = generate.idempotency_record_path(output_dir, task_id)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                '{"version":1,"status":"uncertain","task_id":"task-old-v1-1234",'
                '"created_at_ms":9999999999999,"error":"HTTP 503"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                generate.ImageGenError, "incompatible version"
            ):
                generate.claim_idempotency(output_dir, fingerprint, task_id, 1)
            self.assertTrue(record_path.exists())
            self.assertFalse(record_path.with_suffix(".lock").exists())

    def test_malformed_current_task_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-corrupt-ledger-1234"
            record_path = generate.idempotency_record_path(output_dir, task_id)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text("{not-json", encoding="utf-8")

            with self.assertRaisesRegex(
                generate.ImageGenError, "unreadable or invalid"
            ):
                generate.claim_idempotency(output_dir, "c" * 64, task_id, 1)

            self.assertEqual(record_path.read_text(encoding="utf-8"), "{not-json")
            self.assertFalse(record_path.with_suffix(".lock").exists())

    def test_interrupted_task_claim_returns_recovery_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fingerprint = "r" * 64
            task_id = "task-original-1234"
            record_path = output_dir / ".idempotency" / f"{fingerprint}.json"
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                '{"version":3,"status":"in_progress",'
                f'"task_id":"{task_id}","created_at_ms":'
                + str(generate.time.time_ns() // 1_000_000)
                + ',"request_started_at_ms":1}',
                encoding="utf-8",
            )
            migrated_record, migrated_lock, recovery = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            self.assertIsNotNone(recovery)
            self.assertTrue(recovery.get("_recovery_record"))
            self.assertEqual(recovery.get("task_id"), task_id)
            self.assertEqual(recovery.get("migrated_from_version"), 3)
            self.assertEqual(
                migrated_record,
                generate.idempotency_record_path(output_dir, task_id),
            )
            self.assertFalse(record_path.exists())
            migrated_lock.unlink(missing_ok=True)

    def test_task_result_lookup_never_uses_another_task_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            old_task = "task-current-12345"
            task_id = "task-current-1234"
            fingerprint = "a" * 64
            old_image = task_png_path(output_dir, old_task, fingerprint)
            current_image = task_png_path(output_dir, task_id, fingerprint)
            old_image.write_bytes(png_bytes())
            current_image.write_bytes(png_bytes())
            self.assertEqual(
                generate._task_result_files(
                    output_dir, task_id, fingerprint, expected_count=1
                ),
                [str(current_image.resolve().as_posix())],
            )

    def test_save_images_publishes_under_shared_image_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            image_data = png_bytes()
            actual_sizes = []
            files = generate.save_images(
                {
                    "data": [
                        {
                            "b64_json": generate.base64.b64encode(image_data).decode(
                                "ascii"
                            )
                        }
                    ]
                },
                "https://relay.test/v1/images/generations",
                "secret",
                output_dir,
                30,
                task_id="task-scoped-1234",
                task_fingerprint="f" * 64,
                expected_count=1,
                actual_sizes=actual_sizes,
            )
            self.assertEqual(1, len(files))
            self.assertEqual(["1672×941"], actual_sizes)
            self.assertEqual(Path(files[0]).parent, output_dir.resolve())
            self.assertTrue(
                Path(files[0]).name.startswith(
                    generate._task_image_prefix("task-scoped-1234", "f" * 64)
                )
            )
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
            image = task_png_path(output_dir, task_id, fingerprint)
            image.write_bytes(png_bytes())
            record_path = generate.idempotency_record_path(output_dir, task_id)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                generate.json.dumps(
                    {
                        "version": generate.IDEMPOTENCY_VERSION,
                        "status": "in_progress",
                        "task_id": task_id,
                        "fingerprint": fingerprint,
                        "output_dir": output_dir.resolve().as_posix(),
                        "request_started_at_ms": 1,
                        "request_context": {
                            "mode": "edit",
                            "model": "gpt-image-2",
                            "size": "4K",
                            "requested_size": "4K",
                            "quality": "high",
                            "aspect_ratio": "16:9",
                            "n": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            lock_path = record_path.with_suffix(".lock")
            with mock.patch.object(
                generate, "_schedule_result_hide", return_value=False
            ), mock.patch.object(
                generate, "_schedule_result_cleanup", return_value=True
            ), mock.patch.object(
                generate, "_get_image_request"
            ) as status_get, mock.patch(
                "sys.stdout", io.StringIO()
            ):
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
            self.assertEqual(
                result["display_summary"],
                "实际尺寸：1672×941｜比例：16:9｜画质：high",
            )
            status_get.assert_not_called()

    def test_postprocess_recovery_replays_only_the_local_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            processed_dir = output_dir / "processed"
            processed_dir.mkdir()
            raw = output_dir / "raw.png"
            raw.write_bytes(png_bytes())
            processed_file = processed_dir / "raw_w1920_h1080_cover.png"

            def fake_process(paths, destination, **options):
                self.assertEqual(paths, [raw.resolve().as_posix()])
                self.assertEqual(Path(destination), processed_dir.resolve())
                self.assertEqual(options["output_size"], "1920x1080")
                processed_file.write_bytes(png_bytes(1920, 1080))
                return [{
                    "output": str(processed_file),
                    "output_width": 1920,
                    "output_height": 1080,
                }]

            record = {
                "task_id": "task-postprocess-1234",
                "request_context": {
                    "n": 1,
                    "postprocess": True,
                    "postprocess_config": {
                        "output_dir": processed_dir.resolve().as_posix(),
                        "output_size": "1920x1080",
                        "fit": "cover",
                        "position": "center",
                        "crop": None,
                        "output_format": "png",
                        "output_quality": 90,
                        "output_background": None,
                    },
                },
            }
            with mock.patch.object(
                generate, "process_many", side_effect=fake_process
            ) as process:
                payload = generate._recovery_payload(
                    record, [raw.resolve().as_posix()], output_dir
                )
            process.assert_called_once()
            self.assertEqual(payload["files"], [raw.resolve().as_posix()])
            self.assertEqual(payload["original_files"], [raw.resolve().as_posix()])
            self.assertEqual(
                payload["processed_files"], [processed_file.resolve().as_posix()]
            )
            self.assertEqual(payload["preview_files"], payload["processed_files"])
            self.assertIn("1920×1080", payload["display_summary"])

    def test_same_directory_postprocess_output_is_not_counted_as_raw_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-same-dir-1234"
            fingerprint = "e" * 64
            raw = task_png_path(output_dir, task_id, fingerprint)
            raw.write_bytes(png_bytes())
            processed = raw.with_name(raw.stem + "_w1920_h1080_cover.png")
            processed.write_bytes(png_bytes(1920, 1080))
            self.assertEqual(
                generate._task_result_files(
                    output_dir, task_id, fingerprint, expected_count=1
                ),
                [raw.resolve().as_posix()],
            )

    def test_postprocess_recovery_preserves_multiple_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            processed_dir = output_dir / "processed"
            processed_dir.mkdir()
            raw_files = [output_dir / "raw-1.png", output_dir / "raw-2.png"]
            for raw in raw_files:
                raw.write_bytes(png_bytes())
            processed_files = [
                processed_dir / "raw-1_convert.webp",
                processed_dir / "raw-2_convert.webp",
            ]

            def fake_process(paths, _destination, **_options):
                self.assertEqual(len(paths), 2)
                results = []
                for path in processed_files:
                    path.write_bytes(png_bytes())
                    results.append({
                        "output": str(path),
                        "output_width": 1672,
                        "output_height": 941,
                    })
                return results

            record = {
                "task_id": "task-post-multi-1234",
                "request_context": {
                    "n": 2,
                    "postprocess": True,
                    "postprocess_config": {
                        "output_dir": processed_dir.resolve().as_posix(),
                        "output_size": None,
                        "fit": "cover",
                        "position": "center",
                        "crop": None,
                        "output_format": "webp",
                        "output_quality": 88,
                        "output_background": None,
                    },
                },
            }
            with mock.patch.object(generate, "process_many", side_effect=fake_process):
                payload = generate._recovery_payload(
                    record,
                    [path.resolve().as_posix() for path in raw_files],
                    output_dir,
                )
            self.assertEqual(payload["count"], 2)
            self.assertEqual(
                payload["original_files"],
                [path.resolve().as_posix() for path in raw_files],
            )
            self.assertEqual(
                payload["preview_files"],
                [path.resolve().as_posix() for path in processed_files],
            )

    def test_avif_cached_result_uses_local_header_reader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.avif"
            path.write_bytes(b"avif-placeholder")
            opened = mock.MagicMock()
            opened.__enter__.return_value.size = (1672, 941)
            with mock.patch("PIL.Image.open", return_value=opened):
                self.assertEqual(
                    generate._actual_size_from_image_file(str(path)), "1672×941"
                )
                self.assertTrue(
                    generate._cached_result_files_exist(
                        {"preview_files": [str(path)]}, expected_count=1
                    )
                )

    def test_postprocess_recovery_without_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            raw = output_dir / "raw.png"
            raw.write_bytes(png_bytes())
            record = {
                "task_id": "task-post-missing-1234",
                "request_context": {"n": 1, "postprocess": True},
            }
            with mock.patch.object(generate, "process_many") as process:
                with self.assertRaisesRegex(
                    generate.ImageGenError, "metadata is incomplete"
                ):
                    generate._recovery_payload(
                        record, [raw.resolve().as_posix()], output_dir
                    )
            process.assert_not_called()
            self.assertTrue(raw.exists())

    def test_local_postprocess_stage_persists_original_and_processed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-post-state-1234"
            fingerprint = "a" * 64
            record, lock, _cached = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            generate.update_idempotency_request(
                record,
                task_id,
                output_dir,
                {
                    "n": 1,
                    "postprocess": True,
                    "postprocess_config": {
                        "output_dir": output_dir.resolve().as_posix(),
                    },
                },
            )
            raw = output_dir / "raw.png"
            processed = output_dir / "processed.avif"
            raw.write_bytes(png_bytes())
            processed.write_bytes(b"placeholder")
            generate.update_idempotency_local_stage(
                record,
                task_id,
                [str(raw)],
                processed_files=[str(processed)],
                postprocess_manifest=str(output_dir / "postprocess-manifest.json"),
            )
            saved = generate._load_idempotency_record(record) or {}
            context = saved.get("request_context") or {}
            self.assertEqual(context["original_files"], [raw.resolve().as_posix()])
            self.assertEqual(
                context["processed_files"], [processed.resolve().as_posix()]
            )
            self.assertTrue(context["postprocess_complete"])
            lock.unlink(missing_ok=True)

    def test_same_task_id_with_changed_arguments_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-bound-1234"
            first_fingerprint = "1" * 64
            record, lock, _cached = generate.claim_idempotency(
                output_dir, first_fingerprint, task_id, 1
            )
            generate.finish_idempotency(
                record,
                lock,
                first_fingerprint,
                task_id,
                error="connection outcome unknown",
                uncertain=True,
            )
            with self.assertRaisesRegex(generate.ImageGenError, "different image arguments"):
                generate.claim_idempotency(
                    output_dir, "2" * 64, task_id, 1
                )
            saved = generate._load_idempotency_record(record) or {}
            self.assertEqual(saved.get("fingerprint"), first_fingerprint)
            self.assertFalse(record.with_suffix(".lock").exists())

    def test_global_task_ledger_survives_output_directory_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            first_output = root / "first"
            second_output = root / "second"
            task_id = "task-global-1234"
            fingerprint = "g" * 64
            record, lock, _cached = generate.claim_idempotency(
                first_output,
                fingerprint,
                task_id,
                1,
                state_dir=state_dir,
            )
            generate.update_idempotency_request(
                record,
                task_id,
                first_output,
                {"mode": "generate", "model": "gpt-image-2", "n": 1},
            )
            generate.mark_idempotency_submission(
                record,
                task_id,
                {"task_id": "upstream-global-1234", "status": "queued"},
                "https://relay.test/v1/images/generations",
                {"mode": "generate", "model": "gpt-image-2", "n": 1},
            )
            generate.finish_idempotency(
                record,
                lock,
                fingerprint,
                task_id,
                error="status interrupted",
                uncertain=True,
            )
            record2, lock2, recovery = generate.claim_idempotency(
                second_output,
                fingerprint,
                task_id,
                1,
                state_dir=state_dir,
            )
            self.assertEqual(record2, record)
            self.assertIsNotNone(recovery)
            self.assertEqual(
                Path(str(recovery.get("output_dir"))).resolve(),
                first_output.resolve(),
            )
            self.assertEqual(recovery.get("upstream_task_id"), "upstream-global-1234")
            lock2.unlink(missing_ok=True)

    def test_partial_four_image_result_is_never_treated_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-four-1234"
            fingerprint = "4" * 64
            partial = task_png_path(output_dir, task_id, fingerprint)
            partial.write_bytes(png_bytes())
            with self.assertRaisesRegex(generate.ImageGenError, "incomplete"):
                generate.claim_idempotency(
                    output_dir,
                    fingerprint,
                    task_id,
                    1,
                    expected_count=4,
                )
            self.assertTrue(partial.exists())
            self.assertFalse(
                generate.idempotency_record_path(output_dir, task_id)
                .with_suffix(".lock")
                .exists()
            )

    def test_second_download_failure_removes_staging_and_partial_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-download-1234"
            fingerprint = "d" * 64
            calls = 0

            def fake_download(_url, _endpoint, _key, _timeout, destination):
                nonlocal calls
                calls += 1
                destination.write_bytes(png_bytes())
                if calls == 2:
                    raise generate.ImageGenError("second download failed")
                return png_bytes(), "image/png"

            with mock.patch.object(
                generate, "_download_image_to_path", side_effect=fake_download
            ):
                with self.assertRaisesRegex(generate.ImageGenError, "second download failed"):
                    generate.save_images(
                        {
                            "data": [
                                {"url": "https://example.test/1.png"},
                                {"url": "https://example.test/2.png"},
                            ]
                        },
                        "https://relay.test/v1/images/generations",
                        "secret",
                        output_dir,
                        30,
                        task_id=task_id,
                        task_fingerprint=fingerprint,
                        expected_count=2,
                    )
            self.assertEqual(
                list(output_dir.glob(f"{generate._task_image_prefix(task_id, fingerprint)}*")),
                [],
            )
            self.assertFalse((output_dir / ".staging").exists())

    def test_broken_stdout_keeps_result_and_recovery_records(self) -> None:
        class BrokenStdout:
            def write(self, _value):
                raise BrokenPipeError("consumer disconnected")

            def flush(self):
                raise BrokenPipeError("consumer disconnected")

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-pipe-1234"
            record = generate.idempotency_record_path(output_dir, task_id)
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text("{}", encoding="utf-8")
            with mock.patch.object(generate.sys, "stdout", BrokenStdout()), mock.patch.object(
                generate, "_schedule_result_cleanup"
            ) as cleanup:
                with self.assertRaises(BrokenPipeError):
                    generate.emit_success(
                        {"ok": True, "preview_files": ["image.png"]},
                        output_dir,
                        task_id,
                        1,
                        cleanup_paths=[record],
                    )
            cleanup.assert_not_called()
            self.assertTrue(record.exists())
            self.assertTrue(generate.result_record_path(output_dir, task_id).exists())

    def test_windows_process_probe_never_calls_os_kill(self) -> None:
        with mock.patch.object(generate.os, "name", "nt"), mock.patch.object(
            generate.os, "kill"
        ) as process_kill:
            generate._pid_is_running(999999999)
        process_kill.assert_not_called()

    def test_windows_process_probe_fails_closed_when_exit_query_fails(self) -> None:
        class FakeUInt32:
            def __init__(self) -> None:
                self.value = 0

        class FakeKernel:
            def __init__(self) -> None:
                self.OpenProcess = mock.Mock(return_value=object())
                self.GetExitCodeProcess = mock.Mock(return_value=0)
                self.CloseHandle = mock.Mock(return_value=1)

        kernel = FakeKernel()
        fake_ctypes = types.SimpleNamespace(
            c_uint32=FakeUInt32,
            c_int=int,
            c_void_p=object,
            POINTER=lambda _value: object,
            byref=lambda value: value,
            windll=types.SimpleNamespace(kernel32=kernel),
        )
        with mock.patch.object(generate.os, "name", "nt"), mock.patch.dict(
            sys.modules, {"ctypes": fake_ctypes}
        ):
            self.assertTrue(generate._pid_is_running(12345))
        kernel.GetExitCodeProcess.assert_called_once()
        kernel.CloseHandle.assert_called_once()

    def test_posix_process_probe_fails_closed_on_permission_error(self) -> None:
        with mock.patch.object(generate.os, "name", "posix"), mock.patch.object(
            generate.os, "kill", side_effect=PermissionError("access denied")
        ):
            self.assertTrue(generate._pid_is_running(12345))

    def test_posix_process_probe_recognizes_missing_process(self) -> None:
        with mock.patch.object(generate.os, "name", "posix"), mock.patch.object(
            generate.os, "kill", side_effect=ProcessLookupError("not found")
        ):
            self.assertFalse(generate._pid_is_running(12345))

    def test_concurrent_stale_lock_reaping_allows_only_one_claimant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-stale-race-1234"
            fingerprint = "r" * 64
            record = generate.idempotency_record_path(output_dir, task_id)
            record.parent.mkdir(parents=True)
            lock = record.with_suffix(".lock")
            stale_pid = 2_000_000_001
            lock.write_text(str(stale_pid), encoding="ascii")

            def process_is_running(pid: int) -> bool:
                return pid != stale_pid

            def claim() -> str:
                try:
                    _record, _lock, cached = generate.claim_idempotency(
                        output_dir, fingerprint, task_id, 0.2
                    )
                    self.assertIsNone(cached)
                    return "claimed"
                except generate.ImageGenError as exc:
                    self.assertIn("still running", str(exc))
                    return "blocked"

            with mock.patch.object(
                generate, "_pid_is_running", side_effect=process_is_running
            ), mock.patch.object(
                generate, "IDEMPOTENCY_WAIT_INTERVAL_SECONDS", 0.01
            ), ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda _index: claim(), range(2)))

            self.assertEqual(sorted(outcomes), ["blocked", "claimed"])
            self.assertTrue(lock.exists())
            lock.unlink(missing_ok=True)

    def test_multiple_legacy_records_for_one_task_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            ledger_dir = output_dir / ".idempotency"
            ledger_dir.mkdir()
            task_id = "task-legacy-conflict-1234"
            for index, status in enumerate(("uncertain", "failed"), start=1):
                (ledger_dir / f"legacy-{index}.json").write_text(
                    generate.json.dumps(
                        {
                            "version": 2,
                            "task_id": task_id,
                            "fingerprint": f"{index:064d}",
                            "status": status,
                            "created_at_ms": index,
                        }
                    ),
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(
                generate.ImageGenError, "Multiple legacy recovery records"
            ):
                generate.claim_idempotency(
                    output_dir, "f" * 64, task_id, 1
                )
            current = generate.idempotency_record_path(output_dir, task_id)
            self.assertFalse(current.with_suffix(".lock").exists())

    def test_claim_write_failure_does_not_leave_task_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-write-fail-1234"
            record = generate.idempotency_record_path(output_dir, task_id)
            with mock.patch.object(
                generate,
                "_atomic_write_json",
                side_effect=generate.ImageGenError("disk unavailable"),
            ):
                with self.assertRaisesRegex(generate.ImageGenError, "disk unavailable"):
                    generate.claim_idempotency(
                        output_dir, "w" * 64, task_id, 1
                    )
            self.assertFalse(record.with_suffix(".lock").exists())

    def test_old_uncertain_record_is_not_expired_into_a_second_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-old-unknown-1234"
            fingerprint = "u" * 64
            record = generate.idempotency_record_path(output_dir, task_id)
            generate._atomic_write_json(
                record,
                {
                    "version": generate.IDEMPOTENCY_VERSION,
                    "fingerprint": fingerprint,
                    "status": "uncertain",
                    "task_id": task_id,
                    "created_at_ms": 1,
                    "request_started_at_ms": 1,
                    "upstream_task_id": "upstream-old-1234",
                    "result_endpoint": "https://relay.test/v1/images/generations",
                    "request_context": {"n": 1},
                },
            )
            returned_record, lock, recovery = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            self.assertEqual(returned_record, record)
            self.assertEqual(recovery.get("upstream_task_id"), "upstream-old-1234")
            lock.unlink(missing_ok=True)

    def test_transport_error_is_marked_as_possibly_submitted(self) -> None:
        with mock.patch.object(
            generate.urllib.request,
            "urlopen",
            side_effect=generate.urllib.error.URLError("connection reset"),
        ):
            with self.assertRaises(generate.ImageGenError) as raised:
                generate._post_image_request(
                    "https://relay.test/v1/images/generations",
                    "secret",
                    b"{}",
                    "application/json",
                    30,
                    "i" * 64,
                )
        self.assertTrue(raised.exception.request_may_have_been_sent)
        self.assertFalse(raised.exception.known_terminal)

    def test_submit_http_408_and_5xx_are_ambiguous(self) -> None:
        for status_code in (408, 500, 503, 504, 599):
            with self.subTest(status_code=status_code):
                response = generate.urllib.error.HTTPError(
                    "https://relay.test/v1/images/generations",
                    status_code,
                    "submit failed",
                    {},
                    io.BytesIO(b'{"error":{"message":"submit failed"}}'),
                )
                with mock.patch.object(
                    generate.urllib.request, "urlopen", side_effect=response
                ):
                    with self.assertRaises(generate.ImageGenError) as raised:
                        generate._post_image_request(
                            "https://relay.test/v1/images/generations",
                            "secret",
                            b"{}",
                            "application/json",
                            30,
                            "i" * 64,
                        )
                self.assertTrue(raised.exception.request_may_have_been_sent)
                self.assertFalse(raised.exception.known_terminal)

    def test_submit_http_explicit_4xx_is_terminal(self) -> None:
        for status_code in (400, 401, 403, 413, 422, 429):
            with self.subTest(status_code=status_code):
                response = generate.urllib.error.HTTPError(
                    "https://relay.test/v1/images/generations",
                    status_code,
                    "request rejected",
                    {},
                    io.BytesIO(b'{"error":{"message":"request rejected"}}'),
                )
                with mock.patch.object(
                    generate.urllib.request, "urlopen", side_effect=response
                ):
                    with self.assertRaises(generate.ImageGenError) as raised:
                        generate._post_image_request(
                            "https://relay.test/v1/images/generations",
                            "secret",
                            b"{}",
                            "application/json",
                            30,
                            "i" * 64,
                        )
                self.assertFalse(raised.exception.request_may_have_been_sent)
                self.assertTrue(raised.exception.known_terminal)

    def test_ambiguous_submit_http_error_blocks_same_task_repost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-http-ambiguous-1234"
            fingerprint = "h" * 64
            post_count = 0

            def ambiguous_submit(*_args, **_kwargs):
                nonlocal post_count
                post_count += 1
                raise generate.urllib.error.HTTPError(
                    "https://relay.test/v1/images/generations",
                    503,
                    "gateway unavailable",
                    {},
                    io.BytesIO(b'{"error":{"message":"gateway unavailable"}}'),
                )

            record, lock, cached = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            self.assertIsNone(cached)
            with mock.patch.object(
                generate.urllib.request, "urlopen", side_effect=ambiguous_submit
            ):
                with self.assertRaises(generate.ImageGenError) as raised:
                    generate._post_image_request(
                        "https://relay.test/v1/images/generations",
                        "secret",
                        b"{}",
                        "application/json",
                        30,
                        fingerprint,
                    )
            generate.finish_idempotency(
                record,
                lock,
                fingerprint,
                task_id,
                error=str(raised.exception),
                uncertain=(
                    raised.exception.request_may_have_been_sent
                    and not raised.exception.known_terminal
                ),
            )

            _record2, lock2, recovery = generate.claim_idempotency(
                output_dir, fingerprint, task_id, 1
            )
            self.assertIsNotNone(recovery)
            self.assertTrue(recovery.get("_recovery_record"))
            self.assertEqual(recovery.get("status"), "uncertain")
            self.assertEqual(post_count, 1)
            lock2.unlink(missing_ok=True)

    def test_gpt_image_two_local_variants_are_rejected_before_upload(self) -> None:
        image = Path(tempfile.gettempdir()) / "matrixapi-edit-count-test.png"
        image.write_bytes(png_bytes())
        try:
            with mock.patch.object(generate, "_post_image_request") as post:
                with self.assertRaisesRegex(generate.ImageGenError, "one output"):
                    generate.call_edit_api(
                        "https://relay.test/v1/images/edits",
                        "secret",
                        "gpt-image-2",
                        "edit",
                        "1K",
                        2,
                        [str(image)],
                        None,
                        30,
                    )
            post.assert_not_called()
        finally:
            image.unlink(missing_ok=True)

    def test_recovered_story_image_advances_exact_page_without_status_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-story-recover-1234"
            fingerprint = "s" * 64
            image = task_png_path(output_dir, task_id, fingerprint)
            image.write_bytes(png_bytes())
            story_path = generate.story_state_path(output_dir, "story-recovery-test")
            story_path.parent.mkdir(parents=True, exist_ok=True)
            story_path.write_text(
                generate.json.dumps(
                    {
                        "state_version": generate.STORY_STATE_VERSION,
                        "story_id": story_path.stem,
                        "status": "in_progress",
                        "page": 0,
                        "pending_page": 1,
                        "total_pages": 2,
                        "root_prompt": "story",
                        "output_dir": output_dir.resolve().as_posix(),
                        "model": "gpt-image-2",
                        "size": "1K",
                        "quality": "high",
                        "aspect_ratio": "2:3",
                        "last_original_file": None,
                        "current_task_id": task_id,
                        "next_task_id": None,
                    }
                ),
                encoding="utf-8",
            )
            record_path = generate.idempotency_record_path(output_dir, task_id)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "version": generate.IDEMPOTENCY_VERSION,
                "fingerprint": fingerprint,
                "status": "in_progress",
                "task_id": task_id,
                "output_dir": output_dir.resolve().as_posix(),
                "request_started_at_ms": 1,
                "request_context": {
                    "mode": "generate",
                    "model": "gpt-image-2",
                    "size": "1K",
                    "requested_size": "1K",
                    "quality": "high",
                    "aspect_ratio": "2:3",
                    "n": 1,
                    "story_state_file": story_path.resolve().as_posix(),
                    "story_page": 1,
                },
            }
            generate._atomic_write_json(record_path, record)
            lock_path = record_path.with_suffix(".lock")
            lock_path.write_text(str(generate.os.getpid()), encoding="ascii")
            with mock.patch.object(generate, "_get_image_request") as status_get, mock.patch.object(
                generate, "_schedule_result_cleanup", return_value=True
            ), mock.patch("sys.stdout", io.StringIO()):
                result = generate.recover_submitted_task(
                    record,
                    record_path,
                    lock_path,
                    fingerprint,
                    output_dir,
                    "https://relay.test/v1/images/generations",
                    "secret",
                    30,
                )
            self.assertEqual(result["story"]["page"], 1)
            self.assertEqual(result["story"]["status"], "active")
            state = generate._load_story_state(story_path)
            self.assertEqual(state["last_completed_task_id"], task_id)
            self.assertEqual(state["page"], 1)
            status_get.assert_not_called()

    def test_already_advanced_story_recovery_does_not_advance_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            task_id = "task-story-done-1234"
            image = output_dir / "story-page.png"
            image.write_bytes(png_bytes())
            story_path = generate.story_state_path(output_dir, "story-done-test")
            story_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "state_version": generate.STORY_STATE_VERSION,
                "story_id": story_path.stem,
                "status": "active",
                "page": 1,
                "pending_page": None,
                "total_pages": 3,
                "root_prompt": "story",
                "output_dir": output_dir.resolve().as_posix(),
                "model": "gpt-image-2",
                "size": "1K",
                "quality": "high",
                "aspect_ratio": "2:3",
                "last_original_file": image.resolve().as_posix(),
                "last_completed_task_id": task_id,
                "current_task_id": None,
                "next_task_id": "task-story-next-1234",
            }
            generate._atomic_write_json(story_path, state)
            payload = generate._recovery_payload(
                {
                    "task_id": task_id,
                    "request_context": {
                        "story_state_file": story_path.resolve().as_posix(),
                        "story_page": 1,
                        "n": 1,
                    },
                },
                [image.resolve().as_posix()],
                output_dir,
            )
            self.assertEqual(payload["story"]["page"], 1)
            self.assertEqual(
                generate._load_story_state(story_path)["page"], 1
            )

    def test_main_keeps_story_in_progress_after_unknown_submit_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "images"
            task_id = "task-story-unknown-1234"
            argv = [
                "generate.py",
                "--task-id",
                task_id,
                "--story-pages",
                "2",
                "--prompt",
                "story",
                "--out-dir",
                str(output_dir),
            ]
            failure = generate.ImageGenError(
                "connection reset after submit",
                request_may_have_been_sent=True,
            )
            with mock.patch.object(generate.sys, "argv", argv), mock.patch.object(
                generate.Path, "home", return_value=root / "home"
            ), mock.patch.object(
                generate, "discover_credentials", return_value=("https://matrixapii.com", "key", "gpt-image-2", "test")
            ), mock.patch.object(
                generate, "call_api", side_effect=failure
            ), mock.patch.object(
                generate.sys, "stdout", io.StringIO()
            ), mock.patch.object(
                generate.sys, "stderr", io.StringIO()
            ):
                self.assertEqual(generate.main(), 1)
            story_path = generate.story_state_path(
                output_dir, generate._story_id(task_id)
            )
            state = generate._load_story_state(story_path)
            self.assertEqual(state["status"], "in_progress")
            self.assertEqual(state["current_task_id"], task_id)
            ledger_root = root / "home" / ".codex" / "generated_images" / generate.SKILL_NAME
            record = generate._load_idempotency_record(
                generate.idempotency_record_path(ledger_root, task_id)
            ) or {}
            self.assertEqual(record.get("status"), "uncertain")

    def test_main_lock_timeout_does_not_fail_another_process_story(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "images"
            output_dir.mkdir()
            task_id = "task-story-locked-1234"
            previous = output_dir / "previous.png"
            previous.write_bytes(png_bytes())
            story_path = generate.story_state_path(output_dir, "story-locked-test")
            generate._atomic_write_json(
                story_path,
                {
                    "state_version": generate.STORY_STATE_VERSION,
                    "story_id": story_path.stem,
                    "status": "in_progress",
                    "page": 1,
                    "pending_page": 2,
                    "total_pages": 3,
                    "root_prompt": "story",
                    "output_dir": output_dir.resolve().as_posix(),
                    "model": "gpt-image-2",
                    "size": "1K",
                    "quality": "high",
                    "aspect_ratio": "2:3",
                    "last_original_file": previous.resolve().as_posix(),
                    "last_completed_task_id": "task-story-page1-1234",
                    "current_task_id": task_id,
                    "next_task_id": None,
                },
            )
            argv = [
                "generate.py",
                "--task-id",
                task_id,
                "--story-next",
                str(story_path),
            ]
            with mock.patch.object(generate.sys, "argv", argv), mock.patch.object(
                generate.Path, "home", return_value=root / "home"
            ), mock.patch.object(
                generate, "discover_credentials", return_value=("https://matrixapii.com", "key", "gpt-image-2", "test")
            ), mock.patch.object(
                generate,
                "claim_idempotency",
                side_effect=generate.ImageGenError(
                    "An identical image request is still running"
                ),
            ), mock.patch.object(
                generate.sys, "stdout", io.StringIO()
            ), mock.patch.object(
                generate.sys, "stderr", io.StringIO()
            ):
                self.assertEqual(generate.main(), 1)
            state = generate._load_story_state(story_path)
            self.assertEqual(state["status"], "in_progress")
            self.assertEqual(state["current_task_id"], task_id)


if __name__ == "__main__":
    unittest.main()
