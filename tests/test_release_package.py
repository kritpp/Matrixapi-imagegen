import ast
from pathlib import Path
import unittest
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATE_SCRIPT = (
    REPOSITORY_ROOT / "skills" / "Matrixapi-imagegen" / "scripts" / "generate.py"
)
EXPECTED_PACKAGE_FILES = {
    "README.md",
    "install-macos.command",
    "install-macos.sh",
    "install-windows.bat",
    "install-windows.ps1",
    "skills/Matrixapi-imagegen/SKILL.md",
    "skills/Matrixapi-imagegen/agents/openai.yaml",
    "skills/Matrixapi-imagegen/scripts/generate.py",
    "skills/Matrixapi-imagegen/scripts/update_skill.py",
}


def current_skill_version() -> str:
    module = ast.parse(GENERATE_SCRIPT.read_text(encoding="utf-8"))
    for statement in module.body:
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SKILL_VERSION"
                for target in statement.targets
            )
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            return statement.value.value
    raise AssertionError("SKILL_VERSION was not found in generate.py")


class ReleasePackageTests(unittest.TestCase):
    def test_current_package_contains_exact_release_files_and_installers(self):
        version = current_skill_version()
        package = REPOSITORY_ROOT / f"Matrixapi-imagegen-v{version}.zip"
        self.assertTrue(package.is_file(), f"Current release package is missing: {package.name}")

        with zipfile.ZipFile(package) as archive:
            files = {
                info.filename.replace("\\", "/").removeprefix("./")
                for info in archive.infolist()
                if not info.is_dir()
            }
            self.assertEqual(files, EXPECTED_PACKAGE_FILES)
            for installer in (
                "install-macos.command",
                "install-macos.sh",
                "install-windows.bat",
                "install-windows.ps1",
            ):
                archive_name = next(
                    info.filename
                    for info in archive.infolist()
                    if info.filename.replace("\\", "/").removeprefix("./")
                    == installer
                )
                self.assertGreater(archive.getinfo(archive_name).file_size, 0)


if __name__ == "__main__":
    unittest.main()
