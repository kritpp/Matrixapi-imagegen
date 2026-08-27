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


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate  # noqa: E402
import postprocess  # noqa: E402


class TextEditTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["generate.py", *argv]), redirect_stdout(stdout), redirect_stderr(stderr):
            code = generate.main()
        return code, json.loads(stdout.getvalue().strip() or stderr.getvalue().strip())

    def test_model_text_prompt_quotes_exact_text_and_locks_other_content(self) -> None:
        prompt = generate.build_exact_text_edit_prompt(
            "保持艺术字风格", "青春同频", "牵手情缘", "gpt-image-2-pro"
        )
        self.assertIn('"青春同频"', prompt)
        self.assertIn('"牵手情缘"', prompt)
        self.assertIn("character for character", prompt)
        self.assertIn("all other text unchanged", prompt)
        self.assertIn("GPT Image Pro", prompt)

    def test_precise_text_mode_is_local_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            Image.new("RGB", (500, 240), "white").save(source)
            with (
                patch.object(generate, "discover_credentials") as credentials,
                patch.object(generate, "_schedule_result_hide", return_value=True),
            ):
                code, result = self._run(
                    [
                        "--task-id",
                        "task-precise-text-0001",
                        "--precise-text",
                        "--image",
                        str(source),
                        "--new-text",
                        "牵手情缘",
                        "--text-box",
                        "50,60,400,100",
                        "--out-dir",
                        str(root / "out"),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(result["mode"], "precise-text-local")
            self.assertFalse(result["text_edit"]["api_called"])
            self.assertTrue(Path(result["files"][0]).is_file())
            self.assertTrue(source.is_file())
            credentials.assert_not_called()

    def test_text_box_outside_image_is_rejected_without_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            with patch.object(generate, "discover_credentials") as credentials:
                code, result = self._run(
                    [
                        "--task-id",
                        "task-precise-text-0002",
                        "--precise-text",
                        "--image",
                        str(source),
                        "--new-text",
                        "准确文字",
                        "--text-box",
                        "80,80,50,50",
                        "--out-dir",
                        str(root / "out"),
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("超出", result["error"])
            credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
