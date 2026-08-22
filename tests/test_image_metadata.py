import base64
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "generate.py"
SPEC = importlib.util.spec_from_file_location("matrixapi_imagegen", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ImageMetadataTests(unittest.TestCase):
    def test_png_4k_metadata(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (3840).to_bytes(4, "big") + (2160).to_bytes(4, "big")
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 3840, "height": 2160, "format": "PNG", "resolution": "4K"},
        )

    def test_gif_2k_metadata(self):
        data = b"GIF89a" + (2048).to_bytes(2, "little") + (1152).to_bytes(2, "little")
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 2048, "height": 1152, "format": "GIF", "resolution": "2K"},
        )

    def test_webp_1k_metadata(self):
        data = b"RIFF" + b"\x00\x00\x00\x00WEBPVP8X" + b"\x0a\x00\x00\x00" + b"\x00\x00\x00\x00" + (1023).to_bytes(3, "little") + (767).to_bytes(3, "little")
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 1024, "height": 768, "format": "WEBP", "resolution": "1K"},
        )

    def test_jpeg_2k_metadata(self):
        data = (
            b"\xff\xd8\xff\xc0\x00\x11\x08"
            + (2048).to_bytes(2, "big")
            + (2048).to_bytes(2, "big")
            + b"\x00" * 10
        )
        self.assertEqual(
            MODULE._image_metadata(data),
            {"width": 2048, "height": 2048, "format": "JPEG", "resolution": "2K"},
        )

    def test_save_images_reports_saved_image_metadata(self):
        data = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + (3840).to_bytes(4, "big")
            + (2160).to_bytes(4, "big")
        )
        encoded_image = base64.b64encode(data).decode("ascii")
        result = {"data": [{"b64_json": encoded_image}]}
        with TemporaryDirectory() as output_dir:
            paths, image_info = MODULE.save_images(
                result, "", "", Path(output_dir), 1
            )

            self.assertEqual(len(paths), 1)
            self.assertTrue(Path(paths[0]).is_file())
            self.assertEqual(Path(paths[0]).read_bytes(), data)
            self.assertEqual(
                image_info,
                [{"width": 3840, "height": 2160, "format": "PNG", "resolution": "4K"}],
            )


if __name__ == "__main__":
    unittest.main()
