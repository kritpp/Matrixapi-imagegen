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

    def test_removes_only_a_recognized_legacy_skill_and_preserves_images(self):
        with TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / ".codex" / "skills" / "Matrixapi-imagegen"
            target.mkdir(parents=True)
            legacy = target.parent / "api-imagegen"
            (legacy / "scripts").mkdir(parents=True)
            (legacy / "SKILL.md").write_text("---\nname: api-imagegen\n---\n", encoding="utf-8")
            (legacy / "scripts" / "generate.py").write_text(
                'USER_AGENT = "api-imagegen-skill/1.1"\n', encoding="utf-8"
            )
            historical = root_path / ".codex" / "generated_images" / "api-imagegen" / "old.png"
            historical.parent.mkdir(parents=True)
            historical.write_bytes(b"old image")

            self.assertTrue(MODULE._remove_recognized_legacy_skill(target))
            self.assertFalse(legacy.exists())
            self.assertEqual(historical.read_bytes(), b"old image")

    def test_unrecognized_legacy_directory_is_not_deleted(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "skills" / "Matrixapi-imagegen"
            target.mkdir(parents=True)
            legacy = target.parent / "api-imagegen"
            (legacy / "scripts").mkdir(parents=True)
            (legacy / "SKILL.md").write_text("custom content", encoding="utf-8")
            (legacy / "scripts" / "generate.py").write_text("custom code", encoding="utf-8")

            with self.assertRaises(MODULE.UpdateError):
                MODULE._remove_recognized_legacy_skill(target)
            self.assertTrue(legacy.is_dir())

    def test_replacement_removes_stale_files_from_previous_version(self):
        with TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "Matrixapi-imagegen"
            staged = root_path / "staged-Matrixapi-imagegen"
            target.mkdir()
            staged.mkdir()
            (target / "obsolete.py").write_text("old", encoding="utf-8")
            for relative in MODULE.REQUIRED_FILES:
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("new", encoding="utf-8")

            MODULE._replace_skill(target, staged)

            self.assertFalse((target / "obsolete.py").exists())
            self.assertFalse(staged.exists())
            for relative in MODULE.REQUIRED_FILES:
                self.assertEqual((target / relative).read_text(encoding="utf-8"), "new")

    def test_installed_check_returns_version_current_and_supported_models(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "Matrixapi-imagegen"
            (target / "scripts").mkdir(parents=True)
            script = target / "scripts" / "generate.py"
            script.write_text(
                "import json\n"
                "print(json.dumps({'ok': True, 'skill_version': '1.3.3', "
                "'model': 'gpt-image-2-pro', 'supported_models': "
                "['gpt-image-2', 'gpt-image-2-pro']}))\n",
                encoding="utf-8",
            )

            payload = MODULE._installed_check(target)
            self.assertEqual(payload["skill_version"], "1.3.3")
            self.assertEqual(payload["model"], "gpt-image-2-pro")
            self.assertEqual(payload["supported_models"], ["gpt-image-2", "gpt-image-2-pro"])


if __name__ == "__main__":
    unittest.main()
