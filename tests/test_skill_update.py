import importlib.util
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile


SCRIPT = Path(__file__).parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "update_skill.py"
SPEC = importlib.util.spec_from_file_location("matrixapi_skill_update", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SkillUpdateTests(unittest.TestCase):
    def test_leading_dot_archive_paths_are_accepted(self):
        self.assertEqual(
            MODULE._safe_member_path("./skills/Matrixapi-imagegen/SKILL.md"),
            ("skills", "Matrixapi-imagegen", "SKILL.md"),
        )
        self.assertEqual(
            MODULE._safe_member_path(".\\skills\\Matrixapi-imagegen\\SKILL.md"),
            ("skills", "Matrixapi-imagegen", "SKILL.md"),
        )

    def test_unsafe_archive_paths_are_rejected(self):
        for path in (
            "../outside.txt",
            "skills/../outside.txt",
            "/absolute/path.txt",
            "C:/absolute/path.txt",
        ):
            with self.subTest(path=path), self.assertRaises(MODULE.UpdateError):
                MODULE._safe_member_path(path)

    def test_extracts_skill_from_leading_dot_archive(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            for relative in MODULE.REQUIRED_FILES:
                archive.writestr(
                    f"./skills/Matrixapi-imagegen/{relative}", "test content"
                )

        with TemporaryDirectory() as destination:
            target = Path(destination)
            MODULE._extract_skill(archive_buffer.getvalue(), target)
            for relative in MODULE.REQUIRED_FILES:
                self.assertTrue((target / relative).is_file())


if __name__ == "__main__":
    unittest.main()
