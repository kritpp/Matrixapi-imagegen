from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "Matrixapi-imagegen"


class DistributionTests(unittest.TestCase):
    def test_required_skill_files_and_all_four_installers_exist(self) -> None:
        required = [
            SKILL / "SKILL.md",
            SKILL / "agents" / "openai.yaml",
            SKILL / "scripts" / "generate.py",
            SKILL / "scripts" / "postprocess.py",
            SKILL / "scripts" / "hide_result.py",
            ROOT / "install-windows.bat",
            ROOT / "install-windows.ps1",
            ROOT / "install-macos.command",
            ROOT / "install-macos.sh",
        ]
        self.assertEqual([str(path) for path in required if not path.is_file()], [])

    def test_domain_name_and_version_are_adapted(self) -> None:
        generate = (SKILL / "scripts" / "generate.py").read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('SKILL_VERSION = "1.8.30"', generate)
        self.assertIn('DEFAULT_MODEL = "gpt-image-2"', generate)
        self.assertIn('DEFAULT_BASE_URL = "https://matrixapii.com"', generate)
        self.assertIn('ALLOWED_BASE_HOST = "matrixapii.com"', generate)
        self.assertIn('base_url, key = DEFAULT_BASE_URL, imagegen_key', generate)
        self.assertIn("name: Matrixapi-imagegen", skill)
        self.assertNotIn("auv.666svip.top", generate + skill)


if __name__ == "__main__":
    unittest.main()
