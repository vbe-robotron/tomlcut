from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tomlcut.cli import main


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.mp4"
        self.source.write_bytes(b"not really a video")
        self.config = self.root / "clips.toml"
        self.config.write_text(
            """
output_dir = "out"

[[clips]]
source = "input.mp4"
duration = 1
name = "clip.mp4"
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_validate(self) -> None:
        code, stdout, stderr = self.invoke("validate", str(self.config))
        self.assertEqual(code, 0, stderr)
        self.assertIn("OK: 1 clip", stdout)
        self.assertIn("clip.mp4", stdout)

    def test_validate_invalid_config(self) -> None:
        self.config.write_text("output_dir = 'out'\n", encoding="utf-8")
        code, _, stderr = self.invoke("validate", str(self.config))
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr)

    def test_dry_run(self) -> None:
        code, stdout, stderr = self.invoke("render", "--dry-run", str(self.config))
        self.assertEqual(code, 0, stderr)
        self.assertIn("RENDER", stdout)
        self.assertFalse((self.root / "out").exists())

    def test_init_and_overwrite_protection(self) -> None:
        path = self.root / "generated.toml"
        code, stdout, _ = self.invoke("init", str(path))
        self.assertEqual(code, 0)
        self.assertTrue(path.is_file())
        self.assertIn("Created", stdout)

        code, _, stderr = self.invoke("init", str(path))
        self.assertEqual(code, 2)
        self.assertIn("already exists", stderr)

        code, _, _ = self.invoke("init", "--force", str(path))
        self.assertEqual(code, 0)

    def test_init_template_is_valid_toml(self) -> None:
        import tomllib

        path = self.root / "generated.toml"
        self.invoke("init", str(path))
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        self.assertIn("clips", data)
        self.assertIn("render", data)
        self.assertIn("output_dir", data)

    def test_missing_config_file(self) -> None:
        code, _, stderr = self.invoke("render", str(self.root / "nope.toml"))
        self.assertEqual(code, 2)
        self.assertIn("file not found", stderr)

    def test_quiet_dry_run(self) -> None:
        code, stdout, stderr = self.invoke(
            "render", "--dry-run", "--quiet", str(self.config)
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "")


if __name__ == "__main__":
    unittest.main()
