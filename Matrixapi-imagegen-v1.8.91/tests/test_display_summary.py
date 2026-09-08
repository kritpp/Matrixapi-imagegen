from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "generate.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("matrixapi_generate", SCRIPT)
GENERATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GENERATE)


class DisplaySummaryTests(unittest.TestCase):
    def test_summary_uses_requested_tier_and_actual_pixels(self) -> None:
        self.assertEqual(
            "实际尺寸：3840×2160｜比例：16:9｜画质：high",
            GENERATE.format_display_summary("4K", "3840x2160", "high", "16:9"),
        )

    def test_summary_never_presents_requested_tier_as_actual_size(self) -> None:
        self.assertEqual(
            "实际尺寸：未知｜比例：auto｜画质：auto",
            GENERATE.format_display_summary("1K", "1K", "auto", "auto"),
        )


if __name__ == "__main__":
    unittest.main()
