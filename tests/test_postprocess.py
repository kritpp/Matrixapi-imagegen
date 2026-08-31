from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate  # noqa: E402
from postprocess import PostprocessError, process_image  # noqa: E402


class PostprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.png"
        Image.new("RGBA", (640, 480), (220, 20, 40, 255)).save(self.source)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cover_produces_exact_non_multiple_of_16_dimensions(self) -> None:
        result = process_image(
            self.source,
            self.root / "cover",
            output_size="321x241",
            fit="cover",
        )
        self.assertEqual((result["output_width"], result["output_height"]), (321, 241))
        with Image.open(result["output"]) as output:
            self.assertEqual(output.size, (321, 241))

    def test_contain_preserves_exact_canvas_and_background(self) -> None:
        result = process_image(
            self.source,
            self.root / "contain",
            output_size="300x500",
            fit="contain",
            background_color="#123456",
        )
        with Image.open(result["output"]) as output:
            self.assertEqual(output.size, (300, 500))
            self.assertEqual(output.convert("RGB").getpixel((0, 0)), (18, 52, 86))

    def test_crop_and_jpeg_conversion(self) -> None:
        result = process_image(
            self.source,
            self.root / "crop",
            crop="20,30,100,120",
            output_format="jpeg",
            quality=88,
        )
        with Image.open(result["output"]) as output:
            self.assertEqual(output.size, (100, 120))
            self.assertEqual(output.format, "JPEG")

    def test_crop_outside_source_is_rejected(self) -> None:
        with self.assertRaises(PostprocessError):
            process_image(
                self.source,
                self.root / "invalid",
                crop="600,470,100,100",
            )


class InputAspectRatioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_image(self, name: str, size: tuple[int, int]) -> Path:
        path = self.root / name
        Image.new("RGB", size, (20, 80, 160)).save(path)
        return path

    def test_landscape_input_keeps_auto_for_local_edit(self) -> None:
        source = self._write_image("landscape.png", (1234, 694))
        self.assertEqual(
            generate.resolve_aspect_ratio("auto", [str(source)]),
            ("auto", "input_image"),
        )

    def test_portrait_input_keeps_auto_for_local_edit(self) -> None:
        source = self._write_image("portrait.png", (694, 1234))
        self.assertEqual(
            generate.resolve_aspect_ratio("auto", [str(source)]),
            ("auto", "input_image"),
        )

    def test_square_input_keeps_auto_for_local_edit(self) -> None:
        source = self._write_image("square.png", (1000, 1000))
        self.assertEqual(
            generate.resolve_aspect_ratio("auto", [str(source)]),
            ("auto", "input_image"),
        )

    def test_explicit_ratio_is_not_overridden(self) -> None:
        source = self._write_image("landscape.png", (1234, 694))
        self.assertEqual(
            generate.resolve_aspect_ratio("2:3", [str(source)]),
            ("2:3", "user"),
        )

    def test_invalid_input_dimensions_fail_before_api(self) -> None:
        source = self.root / "not-an-image.png"
        source.write_text("not an image", encoding="utf-8")
        with self.assertRaises(generate.ImageGenError):
            generate.source_preserving_edit_size("4K", [str(source)])

    def test_auto_edit_size_preserves_wide_source_geometry(self) -> None:
        source = self._write_image("banner.png", (952, 376))
        size = generate.source_preserving_edit_size("4K", [str(source)])
        width, height = (int(value) for value in size.split("x"))
        self.assertEqual((width, height), (3840, 1520))
        self.assertLess(abs((width / height) - (952 / 376)), 0.01)


class ModelFailureDiagnosisTests(unittest.TestCase):
    def test_pro_edit_blocks_implicit_local_processing(self) -> None:
        args = type(
            "Args",
            (),
            {
                "output_size": "2048x2048",
                "crop": None,
                "output_format": "same",
                "output_quality": None,
                "output_background": None,
                "allow_pro_postprocess": False,
            },
        )()
        with self.assertRaises(generate.ImageGenError) as raised:
            generate.validate_pro_edit_processing("gemini-3-pro-image", "edit", True, args)
        self.assertIn("不会扣费", str(raised.exception))

    def test_pro_edit_allows_explicit_local_processing(self) -> None:
        args = type(
            "Args",
            (),
            {
                "output_size": "2048x2048",
                "crop": None,
                "output_format": "same",
                "output_quality": None,
                "output_background": None,
                "allow_pro_postprocess": True,
            },
        )()
        generate.validate_pro_edit_processing("gemini-3-pro-image", "edit", True, args)

    def test_pro_edit_blocks_implicit_fixed_ratio(self) -> None:
        args = type(
            "Args",
            (),
            {
                "aspect_ratio": "3:2",
                "output_size": None,
                "crop": None,
                "output_format": "same",
                "output_quality": None,
                "output_background": None,
                "allow_pro_postprocess": False,
            },
        )()
        with self.assertRaises(generate.ImageGenError):
            generate.validate_pro_edit_processing("gemini-3-pro-image", "edit", True, args)

    def test_4k_edit_is_not_changed_by_pro_guard(self) -> None:
        args = type(
            "Args",
            (),
            {
                "output_size": "2048x2048",
                "crop": None,
                "output_format": "same",
                "output_quality": None,
                "output_background": None,
                "allow_pro_postprocess": False,
            },
        )()
        generate.validate_pro_edit_processing("gpt-image-2", "edit", True, args)

    def test_gpt_image_mask_is_disabled_without_explicit_capability(self) -> None:
        with patch.dict("os.environ", {"IMAGEGEN_MASK_SUPPORT": "0"}):
            self.assertFalse(generate.mask_support_enabled("gpt-image-2"))

    def test_gpt_image_mask_requires_explicit_capability(self) -> None:
        with patch.dict("os.environ", {"IMAGEGEN_MASK_SUPPORT": "1"}):
            self.assertTrue(generate.mask_support_enabled("gpt-image-2"))

    def test_other_models_keep_provider_mask_behavior(self) -> None:
        with patch.dict("os.environ", {"IMAGEGEN_MASK_SUPPORT": "0"}):
            self.assertTrue(generate.mask_support_enabled("other-image-model"))

    def test_generic_400_is_not_claimed_as_copyright(self) -> None:
        category, message = generate._diagnose_upstream_failure(
            '{"message":"request failed","type":"bad_response_status_code"}',
            400,
        )
        self.assertEqual(category, "upstream_rejection_unknown")
        self.assertIn("无法确认是否版权", message)
        self.assertNotIn("上游", message)

    def test_explicit_policy_and_model_route_are_distinct(self) -> None:
        policy, policy_message = generate._diagnose_upstream_failure(
            '{"message":"copyright policy violation"}', 400
        )
        route, route_message = generate._diagnose_upstream_failure(
            '{"code":"model_not_found","message":"无可用渠道"}', 503
        )
        self.assertEqual(policy, "content_policy")
        self.assertEqual(route, "model_route")
        self.assertIn("模型明确拒绝", policy_message)
        self.assertNotIn("上游", policy_message)
        self.assertNotIn("上游", route_message)


if __name__ == "__main__":
    unittest.main()
