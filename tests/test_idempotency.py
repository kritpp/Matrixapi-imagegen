from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import generate  # noqa: E402


class IdempotencyTests(unittest.TestCase):
    def test_fingerprint_binds_thread_and_reference_order_and_content(self) -> None:
        with TemporaryDirectory() as root:
            first = Path(root) / "first.png"
            second = Path(root) / "second.png"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            request = {
                "prompt": "same story",
                "model": "gpt-image-2",
                "mode": "edit",
                "size": "2048x2048",
                "quality": "high",
                "n": 1,
                "references": generate._local_reference_fingerprint(
                    [str(first), str(second)], None
                ),
            }
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-a"}, clear=False):
                original = generate.request_fingerprint("task-one-0001", request)
                reordered = dict(request)
                reordered["references"] = generate._local_reference_fingerprint(
                    [str(second), str(first)], None
                )
                changed = generate.request_fingerprint("task-two-0002", reordered)
            self.assertNotEqual(original, changed)
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-b"}, clear=False):
                other_thread = generate.request_fingerprint("task-one-0001", request)
            self.assertNotEqual(original, other_thread)

    def test_success_is_reused_without_second_claim(self) -> None:
        with TemporaryDirectory() as root:
            output = Path(root)
            image = output / "image.png"
            image.write_bytes(b"image")
            fingerprint = "a" * 64
            record, lock, cached = generate.claim_idempotency(
                output, fingerprint, "task-first-0001", 10
            )
            self.assertIsNone(cached)
            payload = {"ok": True, "task_id": "task-first-0001", "preview_files": [str(image)]}
            generate.finish_idempotency(record, lock, fingerprint, "task-first-0001", payload=payload)
            _, _, reused = generate.claim_idempotency(
                output, fingerprint, "task-second-0002", 10
            )
            self.assertEqual(reused, payload)

    def test_unknown_state_blocks_duplicate_submission(self) -> None:
        with TemporaryDirectory() as root:
            output = Path(root)
            fingerprint = "b" * 64
            record, lock, _ = generate.claim_idempotency(
                output, fingerprint, "task-first-0001", 10
            )
            generate.finish_idempotency(
                record, lock, fingerprint, "task-first-0001", error="timeout", uncertain=True
            )
            with self.assertRaises(generate.ImageGenError):
                generate.claim_idempotency(output, fingerprint, "task-second-0002", 10)


if __name__ == "__main__":
    unittest.main()
