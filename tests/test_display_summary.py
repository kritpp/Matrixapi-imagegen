from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
import tempfile
import hashlib
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "generate.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("matrixapi_generate", SCRIPT)
GENERATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GENERATE)


class DisplaySummaryTests(unittest.TestCase):
    def test_summary_uses_requested_tier_and_actual_pixels(self) -> None:
        self.assertEqual(
            "实际尺寸：3840×2160｜实际比例：16:9｜请求比例：16:9",
            GENERATE.format_display_summary("4K", "3840x2160", "high", "16:9"),
        )

    def test_summary_never_presents_requested_tier_as_actual_size(self) -> None:
        self.assertEqual(
            "实际尺寸：未知｜实际比例：未知｜请求比例：默认",
            GENERATE.format_display_summary("1K", "1K", "auto", "auto"),
        )

    def test_reported_screenshot_shape_is_not_labeled_as_sixteen_nine(self):
        detail = GENERATE.dimension_metadata("508×337", "16：9")
        self.assertEqual(detail["actual_aspect_ratio"], "508:337")
        self.assertFalse(detail["aspect_ratio_exact_match"])
        summary = GENERATE.format_display_summary("1K", "508x337", "high", "16:9")
        self.assertIn("实际比例：约1.51:1", summary)
        self.assertIn("请求比例：16:9", summary)
        self.assertIn("不完全一致", summary)

    def test_recover_old_summary_and_preserve_every_gif_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "original.gif"
            frames = [Image.new("RGB", (508, 337), color) for color in ("red", "green", "blue")]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=180, loop=0)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            payload = {"ok": True, "api_output_format": "gif", "aspect_ratio": "16:9",
                       "files": [str(path)], "preview_files": [str(path)],
                       "display_summary": "实际尺寸：508×337｜比例：16:9"}
            GENERATE.apply_media_metadata(payload)
            GENERATE.apply_media_metadata(payload)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["animation_controls_satisfied"])
            self.assertFalse(payload["requested_output_satisfied"])
            self.assertFalse(payload["requested_aspect_ratio_satisfied"])
            self.assertEqual(payload["media"][0]["frame_count"], 3)
            self.assertEqual(payload["display_summary"].count("GIF 动图"), 1)
            self.assertIn("请求比例：16:9", payload["display_summary"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_default_and_multiple_output_dimensions_stay_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "wide.png", Path(tmp) / "tall.png"]
            for path, size in zip(paths, [(160, 90), (90, 160)]):
                Image.new("RGB", size, "blue").save(path)
            payload = {"ok": True, "aspect_ratio": "auto", "files": list(map(str, paths))}
            GENERATE.apply_media_metadata(payload)
            self.assertEqual(payload["actual_aspect_ratios"], ["16:9", "9:16"])
            self.assertIsNone(payload["requested_aspect_ratio_satisfied"])
            self.assertNotIn("aspect_ratio_warning", payload)
            self.assertEqual(len(payload["display_summaries"]), 2)


if __name__ == "__main__":
    unittest.main()
