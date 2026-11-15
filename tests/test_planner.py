from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tomlcut.config import MANIFEST_NAME, Clip, Config, RenderOptions
from tomlcut.manifest import Entry, Manifest
from tomlcut.planner import (
    PlanError,
    build_plan,
    fingerprint,
    render_key,
)


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output_dir = self.root / "out"
        self.output_dir.mkdir()
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source data")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_config(
        self,
        clips: list[Clip],
        *,
        dedupe: bool = True,
        render: RenderOptions | None = None,
        output_dir: Path | None = None,
    ) -> Config:
        indexed = [replace(clip, index=index) for index, clip in enumerate(clips)]
        return Config(
            path=self.root / "clips.toml",
            output_dir=output_dir or self.output_dir,
            dedupe=dedupe,
            render=render or RenderOptions(),
            clips=tuple(indexed),
        )

    def make_clip(
        self,
        *,
        name: str = "clip.mp4",
        start: float = 1.0,
        end: float = 2.0,
        extra_args: tuple[str, ...] = (),
    ) -> Clip:
        return Clip(
            index=0,
            source=self.source,
            start=start,
            end=end,
            name=name,
            extra_args=extra_args,
        )

    def manifest(self) -> Manifest:
        return Manifest.load(self.output_dir)

    def record(
        self,
        item_key: str,
        *,
        output: str = "clip.mp4",
        start: float = 1.0,
        end: float = 2.0,
    ) -> None:
        manifest = self.manifest()
        info = self.source.stat()
        manifest.add(
            Entry(
                key=item_key,
                source=str(self.source),
                source_size=info.st_size,
                source_mtime_ns=info.st_mtime_ns,
                start=start,
                end=end,
                output=output,
                rendered_at="2026-01-01T00:00:00+00:00",
            )
        )
        manifest.save()

    def test_fresh_target_is_rendered(self) -> None:
        item = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )[0]
        self.assertEqual(item.action, "render")
        self.assertEqual(item.reason, "new clip")
        self.assertEqual(item.target, self.output_dir / "clip.mp4")

    def test_existing_managed_output_is_skipped(self) -> None:
        item = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )[0]
        (self.output_dir / "clip.mp4").write_bytes(b"rendered")
        self.record(item.key)

        plan = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )
        self.assertEqual(plan[0].action, "skip")
        self.assertEqual(plan[0].reason, "already rendered")

    def test_unmanaged_existing_output_is_skipped(self) -> None:
        (self.output_dir / "clip.mp4").write_bytes(b"existing")
        item = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )[0]
        self.assertEqual(item.action, "skip")
        self.assertIn("not in manifest", item.reason)

    def test_unmanaged_existing_output_is_overwritten_without_dedupe(self) -> None:
        (self.output_dir / "clip.mp4").write_bytes(b"existing")
        item = build_plan(
            self.make_config([self.make_clip()], dedupe=False),
            self.manifest(),
            dedupe=False,
        )[0]
        self.assertEqual(item.action, "render")
        self.assertIn("dedupe disabled", item.reason)

    def test_changed_source_rerenders_managed_output(self) -> None:
        item = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )[0]
        (self.output_dir / "clip.mp4").write_bytes(b"old render")
        self.record(item.key)

        self.source.write_bytes(b"source data changed")
        plan = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )
        self.assertEqual(plan[0].action, "render")
        self.assertIn("changed", plan[0].reason)

    def test_missing_output_is_rendered_again(self) -> None:
        item = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )[0]
        self.record(item.key)

        plan = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )
        self.assertEqual(plan[0].action, "render")
        self.assertIn("missing", plan[0].reason)

    def test_empty_output_is_not_treated_as_complete(self) -> None:
        item = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )[0]
        (self.output_dir / "clip.mp4").write_bytes(b"")
        self.record(item.key)

        plan = build_plan(
            self.make_config([self.make_clip()]), self.manifest(), dedupe=True
        )
        self.assertEqual(plan[0].action, "render")

    def test_duplicate_definitions_render_once(self) -> None:
        clip = self.make_clip()
        plan = build_plan(self.make_config([clip, clip]), self.manifest(), dedupe=True)
        self.assertEqual(plan[0].action, "render")
        self.assertEqual(plan[1].action, "skip")
        self.assertEqual(plan[1].reason, "duplicate clip definition")
        self.assertEqual(plan[1].duplicate_of, 0)

    def test_duplicate_definitions_render_twice_without_dedupe(self) -> None:
        clip = self.make_clip()
        plan = build_plan(
            self.make_config([clip, clip], dedupe=False),
            self.manifest(),
            dedupe=False,
        )
        self.assertEqual([item.action for item in plan], ["render", "render"])

    def test_target_directory_is_an_error(self) -> None:
        (self.output_dir / "clip.mp4").mkdir()
        with self.assertRaisesRegex(PlanError, "is a directory"):
            build_plan(
                self.make_config([self.make_clip()]), self.manifest(), dedupe=True
            )

    def test_output_dir_file_is_an_error(self) -> None:
        path = self.root / "not-a-dir"
        path.write_bytes(b"x")
        with self.assertRaisesRegex(PlanError, "not a directory"):
            build_plan(
                self.make_config([self.make_clip()], output_dir=path),
                self.manifest(),
                dedupe=True,
            )

    def test_key_is_stable(self) -> None:
        clip = self.make_clip()
        render = RenderOptions()
        first = render_key(clip, render, fingerprint(clip))
        second = render_key(clip, render, fingerprint(clip))
        self.assertEqual(first, second)

    def test_key_changes_with_inputs_and_settings(self) -> None:
        clip = self.make_clip()
        render = RenderOptions()
        base = render_key(clip, render, fingerprint(clip))

        variants = {
            "start": (replace(clip, start=1.5), render),
            "end": (replace(clip, end=2.5), render),
            "name": (replace(clip, name="other.mp4"), render),
            "clip args": (replace(clip, extra_args=("-an",)), render),
            "crf": (clip, replace(render, crf=20)),
            "preset": (clip, replace(render, preset="slow")),
            "audio": (clip, replace(render, audio_bitrate="128k")),
            "mode": (clip, replace(render, mode="copy")),
            "ffmpeg": (clip, replace(render, ffmpeg="/opt/ffmpeg")),
            "render args": (clip, replace(render, extra_args=("-threads", "2"))),
        }
        for name, (variant_clip, variant_render) in variants.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    base,
                    render_key(
                        variant_clip,
                        variant_render,
                        fingerprint(variant_clip),
                    ),
                )

    def test_key_changes_when_source_changes(self) -> None:
        clip = self.make_clip()
        render = RenderOptions()
        base = render_key(clip, render, fingerprint(clip))

        self.source.write_bytes(b"larger source data")
        changed_size = render_key(clip, render, fingerprint(clip))
        self.assertNotEqual(base, changed_size)

        info = self.source.stat()
        os.utime(self.source, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        self.assertNotEqual(changed_size, render_key(clip, render, fingerprint(clip)))

    def test_manifest_path_inside_output_dir(self) -> None:
        config = self.make_config([self.make_clip()])
        self.assertEqual(config.manifest_path, self.output_dir / MANIFEST_NAME)


if __name__ == "__main__":
    unittest.main()
