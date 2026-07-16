"""Release-facing naming and packaging invariants for Quick SDF Paint."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "quick_sdf_blender"


class ReleaseNamingTests(unittest.TestCase):
    def test_manifest_and_bl_info_are_080_quick_sdf_paint(self) -> None:
        manifest_source = (ADDON / "blender_manifest.toml").read_text(encoding="utf-8")

        def manifest_string(key: str) -> str:
            line = next(
                value for value in manifest_source.splitlines()
                if value.startswith(f"{key} = ")
            )
            return str(ast.literal_eval(line.split("=", 1)[1].strip()))

        self.assertEqual(manifest_string("id"), "quick_sdf_blender")
        self.assertEqual(manifest_string("name"), "Quick SDF Paint")
        self.assertEqual(manifest_string("version"), "0.8.0")
        self.assertEqual(manifest_string("blender_version_min"), "5.1.0")
        self.assertEqual(manifest_string("blender_version_max"), "5.3.0")
        self.assertIn("threshold map", manifest_string("tagline").lower())

        module = ast.parse((ADDON / "__init__.py").read_text(encoding="utf-8"))
        assignment = next(
            node for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets)
        )
        bl_info = ast.literal_eval(assignment.value)
        self.assertEqual(bl_info["name"], "Quick SDF Paint")
        self.assertEqual(bl_info["version"], (0, 8, 0))
        self.assertIn("threshold map", bl_info["description"].lower())

    def test_current_sources_use_quick_sdf_paint_name(self) -> None:
        occurrences: list[tuple[str, int]] = []
        for path in ADDON.rglob("*"):
            if path.suffix not in {".py", ".toml", ".md"}:
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "Quick SDF Studio" in line:
                    occurrences.append((path.relative_to(ADDON).as_posix(), line_number))
        self.assertEqual(occurrences, [])

    def test_public_sources_do_not_present_face_sdf_as_the_output(self) -> None:
        violations: list[str] = []
        for path in ADDON.rglob("*"):
            if path.suffix not in {".py", ".toml", ".md"}:
                continue
            source = path.read_text(encoding="utf-8").lower()
            if "face sdf" in source or "face-sdf" in source:
                violations.append(path.relative_to(ADDON).as_posix())
        self.assertEqual(violations, [])

    def test_readme_places_sdf_terminology_after_the_user_workflow(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        workflow = readme.index("## 5つの操作")
        export = readme.index("## スレッショルドマップの出力")
        terminology = readme.index("## 出力とSDF")
        development = readme.index("## Development")

        self.assertLess(workflow, export)
        self.assertLess(export, terminology)
        self.assertLess(terminology, development)

    def test_bundled_native_uses_current_abi(self) -> None:
        installed_smoke = (ROOT / "tests" / "blender_installed_extension_smoke.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("native.version() == 8", installed_smoke)


if __name__ == "__main__":
    unittest.main()
