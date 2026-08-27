import importlib.util
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
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

    def test_archive_version_is_read_from_the_package_filename(self):
        self.assertEqual(
            MODULE._archive_version(
                "https://github.com/kritpp/Matrixapi-imagegen/raw/main/Matrixapi-imagegen-v1.8.9.zip"
            ),
            "1.8.9",
        )
        with self.assertRaises(MODULE.UpdateError):
            MODULE._archive_version("https://example.invalid/latest.zip")

    def test_staged_skill_requires_matching_version_and_fixed_url(self):
        with TemporaryDirectory() as root:
            staged = Path(root)
            for relative in MODULE.REQUIRED_FILES:
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("placeholder", encoding="utf-8")
            (staged / "SKILL.md").write_text(
                "---\nname: Matrixapi-imagegen\n---\n", encoding="utf-8"
            )
            (staged / "scripts" / "generate.py").write_text(
                'SKILL_VERSION = "1.8.9"\n'
                'DEFAULT_BASE_URL = "https://matrixapii.com"\n'
                'ALLOWED_BASE_HOST = "matrixapii.com"\n',
                encoding="utf-8",
            )
            MODULE._validate_staged_skill(staged, "1.8.9")
            with self.assertRaises(MODULE.UpdateError):
                MODULE._validate_staged_skill(staged, "1.8.10")

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

    def test_failed_replacement_can_be_rolled_back(self):
        with TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "Matrixapi-imagegen"
            staged = root_path / "staged-Matrixapi-imagegen"
            target.mkdir()
            staged.mkdir()
            (target / "old.txt").write_text("old version", encoding="utf-8")
            for relative in MODULE.REQUIRED_FILES:
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("new version", encoding="utf-8")

            backup = MODULE._replace_skill(target, staged, keep_backup=True)
            self.assertIsNotNone(backup)
            MODULE._rollback_skill(target, backup)

            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old version")
            self.assertFalse((target / "SKILL.md").exists())

    def test_installed_check_returns_version_current_and_supported_models(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "Matrixapi-imagegen"
            (target / "scripts").mkdir(parents=True)
            script = target / "scripts" / "generate.py"
            script.write_text(
                "import json\n"
                "print(json.dumps({'ok': True, 'version': '1.8.9', "
                "'model': 'gpt-image-2-pro', 'supported_models': "
                "['gpt-image-2', 'gpt-image-2-pro']}))\n",
                encoding="utf-8",
            )

            payload = MODULE._installed_check(target)
            self.assertEqual(payload["skill_version"], "1.8.9")
            self.assertEqual(payload["model"], "gpt-image-2-pro")
            self.assertEqual(payload["supported_models"], ["gpt-image-2", "gpt-image-2-pro"])

    def test_local_release_updates_atomically_without_touching_external_data(self):
        package = Path(__file__).parents[1] / "Matrixapi-imagegen-v1.8.11.zip"
        with TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "skills" / "Matrixapi-imagegen"
            target.mkdir(parents=True)
            (target / "old-only.txt").write_text("old", encoding="utf-8")
            external_config = root_path / "Matrixapi-imagegen.env"
            external_image = root_path / "generated_images" / "old.png"
            external_config.write_text("IMAGEGEN_API_KEY=unchanged", encoding="utf-8")
            external_image.parent.mkdir()
            external_image.write_bytes(b"historical image")

            with mock.patch.dict(
                os.environ,
                {"IMAGEGEN_API_KEY": "local-update-check", "IMAGEGEN_MODEL": "gpt-image-2"},
                clear=False,
            ):
                installed = MODULE.update_skill(package.resolve().as_uri(), target)

            self.assertEqual(installed["skill_version"], "1.8.11")
            self.assertFalse((target / "old-only.txt").exists())
            self.assertTrue((target / "scripts" / "update_skill.py").is_file())
            self.assertEqual(external_config.read_text(encoding="utf-8"), "IMAGEGEN_API_KEY=unchanged")
            self.assertEqual(external_image.read_bytes(), b"historical image")


if __name__ == "__main__":
    unittest.main()
