from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tomlcut.config import MANIFEST_NAME
from tomlcut.manifest import Entry, Manifest, ManifestError


def make_entry(key: str = "key-1", output: str = "clip.mp4") -> Entry:
    return Entry(
        key=key,
        source="/videos/source.mp4",
        source_size=1234,
        source_mtime_ns=5678,
        start=1.0,
        end=2.0,
        output=output,
        rendered_at="2026-01-01T00:00:00+00:00",
        command=("ffmpeg", "-i", "source.mp4", "clip.mp4"),
    )


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary.name) / "out"
        self.output_dir.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_manifest_is_empty(self) -> None:
        manifest = Manifest.load(self.output_dir)
        self.assertEqual(manifest.entries, {})
        self.assertEqual(manifest.path, self.output_dir / MANIFEST_NAME)

    def test_round_trip(self) -> None:
        manifest = Manifest.load(self.output_dir)
        manifest.add(make_entry())
        manifest.save()

        loaded = Manifest.load(self.output_dir)
        self.assertEqual(loaded.entries["key-1"], make_entry())
        self.assertEqual(loaded.find_by_output("clip.mp4"), make_entry())

    def test_add_replaces_stale_entries_for_same_output(self) -> None:
        manifest = Manifest.load(self.output_dir)
        manifest.add(make_entry("old-key", "clip.mp4"))
        manifest.add(make_entry("new-key", "clip.mp4"))

        self.assertNotIn("old-key", manifest.entries)
        self.assertIn("new-key", manifest.entries)
        self.assertEqual(len(manifest.entries), 1)

    def test_multiple_outputs_are_kept(self) -> None:
        manifest = Manifest.load(self.output_dir)
        manifest.add(make_entry("key-1", "a.mp4"))
        manifest.add(make_entry("key-2", "b.mp4"))
        self.assertEqual(len(manifest.entries), 2)

    def test_invalid_json(self) -> None:
        (self.output_dir / MANIFEST_NAME).write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "invalid JSON"):
            Manifest.load(self.output_dir)

    def test_unsupported_version(self) -> None:
        (self.output_dir / MANIFEST_NAME).write_text(
            json.dumps({"version": 99, "renders": {}}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ManifestError, "unsupported manifest version"):
            Manifest.load(self.output_dir)

    def test_invalid_renders_shape(self) -> None:
        (self.output_dir / MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "renders": []}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ManifestError, "renders"):
            Manifest.load(self.output_dir)

    def test_invalid_entry(self) -> None:
        (self.output_dir / MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "renders": {"key": {"source": 1}}}),
            encoding="utf-8",
        )
        with self.assertRaises(ManifestError):
            Manifest.load(self.output_dir)

    def test_save_creates_output_directory(self) -> None:
        target = Path(self.temporary.name) / "new" / "out"
        manifest = Manifest(target / MANIFEST_NAME)
        manifest.add(make_entry())
        manifest.save()
        self.assertTrue((target / MANIFEST_NAME).is_file())

    def test_saved_json_is_readable_and_versioned(self) -> None:
        manifest = Manifest.load(self.output_dir)
        manifest.add(make_entry())
        manifest.save()
        raw = json.loads((self.output_dir / MANIFEST_NAME).read_text("utf-8"))
        self.assertEqual(raw["version"], 1)
        self.assertEqual(raw["renders"]["key-1"]["output"], "clip.mp4")


if __name__ == "__main__":
    unittest.main()
