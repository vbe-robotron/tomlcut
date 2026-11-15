"""Command line interface for tomlcut."""

from __future__ import annotations

import argparse
import shlex
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config
from .ffmpeg import FFmpegError, check_ffmpeg, render_clip
from .manifest import Entry, Manifest, ManifestError
from .planner import PlanError, PlanItem, build_plan
from .timecode import format_timecode

_INIT_TEMPLATE = """\
# tomlcut configuration
# パスはこの TOML ファイルからの相対パス、または絶対パスで指定できます。

output_dir = "./clips"

# 同じ入力・範囲・出力名・設定のレンダリング結果が既にある場合は
# レンダリングをスキップします。作り直すには --no-dedupe を指定します。
dedupe = true

[render]
# "encode": H.264/AAC で再エンコード（正確な切り出し）
# "copy":   ストリームコピー（速いがキーフレーム単位でしか切れない）
mode = "encode"
preset = "medium"
crf = 18
audio_bitrate = "192k"

[[clips]]
source = "./input.mp4"
start = "00:00:10"
duration = 12.5
name = "clip-01.mp4"

[[clips]]
source = "./input.mp4"
start = 30
end = 45
name = "clip-02.mp4"
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tomlcut",
        description="Cut video clips with FFmpeg from a TOML definition.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render",
        help="render clips defined in a TOML file",
        description="Render the clips defined in a TOML file.",
    )
    render_parser.add_argument("config", type=Path, help="path to the TOML file")
    render_parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="override output_dir from the TOML file",
    )
    render_parser.add_argument(
        "--ffmpeg", help="FFmpeg executable to use (default: ffmpeg)"
    )
    render_parser.add_argument(
        "--dedupe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="skip clips that were already rendered (default: TOML setting)",
    )
    render_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be rendered without running FFmpeg",
    )
    render_parser.add_argument(
        "--fail-fast", action="store_true", help="stop after the first failure"
    )
    render_parser.add_argument(
        "-q", "--quiet", action="store_true", help="only print errors"
    )
    render_parser.add_argument(
        "-v", "--verbose", action="store_true", help="print FFmpeg commands"
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate a TOML file without rendering",
        description="Load and validate a TOML file without rendering anything.",
    )
    validate_parser.add_argument("config", type=Path, help="path to the TOML file")
    validate_parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="override output_dir from the TOML file",
    )
    validate_parser.add_argument(
        "--ffmpeg", help="FFmpeg executable to use (default: ffmpeg)"
    )

    init_parser = subparsers.add_parser(
        "init",
        help="write an example TOML file",
        description="Write an example TOML file.",
    )
    init_parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("clips.toml"),
        help="path to write (default: clips.toml)",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing file"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "render":
            return _run_render(args)
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "init":
            return _run_init(args)
        parser.error(f"unknown command: {args.command}")
    except (ConfigError, ManifestError, PlanError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FFmpegError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0


def _run_render(args: argparse.Namespace) -> int:
    config = load_config(
        args.config,
        output_dir=args.output_dir,
        ffmpeg=args.ffmpeg,
    )
    dedupe = config.dedupe if args.dedupe is None else args.dedupe

    if not args.dry_run:
        version = check_ffmpeg(config.render.ffmpeg)
        if args.verbose and not args.quiet:
            print(version)

    manifest = Manifest.load(config.output_dir)
    items = build_plan(config, manifest, dedupe=dedupe)

    if args.dry_run:
        return _print_dry_run(config, items, dedupe=dedupe, quiet=args.quiet)

    to_render = sum(1 for item in items if item.should_render)
    skipped = len(items) - to_render
    if not args.quiet:
        print(f"Config:  {config.path}")
        print(f"Output:  {config.output_dir}")
        print(f"Dedupe:  {'on' if dedupe else 'off'}")
        print(f"Clips:   {len(items)} total, {to_render} to render, {skipped} to skip")

    rendered = 0
    skipped_count = 0
    failures = 0
    started = time.monotonic()
    total = len(items)

    for position, item in enumerate(items, start=1):
        label = f"[{position}/{total}] {item.clip.name}"

        if not item.should_render:
            skipped_count += 1
            if not args.quiet:
                print(f"{label}: skipped ({item.reason})")
            continue

        if args.verbose and not args.quiet:
            print(
                f"{label}: {format_timecode(item.clip.start)}"
                f" - {format_timecode(item.clip.end)} ({item.reason})"
            )

        interactive = sys.stderr.isatty() and not args.quiet
        if interactive:
            sys.stderr.write(f"{label}:   0%")
            sys.stderr.flush()
        elif not args.quiet:
            print(f"{label}: rendering ({item.reason})")

        last_percent = -1

        def progress(
            fraction: float,
            *,
            label: str = label,
            interactive: bool = interactive,
        ) -> None:
            nonlocal last_percent
            if not interactive:
                return
            percent = max(0, min(100, int(fraction * 100)))
            if percent == last_percent:
                return
            last_percent = percent
            sys.stderr.write(f"\r{label}: {percent:3d}%")
            sys.stderr.flush()

        clip_started = time.monotonic()
        try:
            command = render_clip(
                config,
                item.clip,
                item.target,
                on_progress=progress,
            )
        except FFmpegError as exc:
            failures += 1
            if interactive:
                sys.stderr.write("\n")
            print(f"{label}: FAILED\n{exc}", file=sys.stderr)
            if args.fail_fast:
                break
            continue

        elapsed = time.monotonic() - clip_started
        manifest.add(
            Entry(
                key=item.key,
                source=str(item.source.path),
                source_size=item.source.size,
                source_mtime_ns=item.source.mtime_ns,
                start=item.clip.start,
                end=item.clip.end,
                output=item.clip.name,
                rendered_at=datetime.now(UTC).isoformat(timespec="seconds"),
                command=command,
            )
        )
        manifest.save()
        rendered += 1

        done = f"{label}: done in {elapsed:.1f}s"
        if interactive:
            sys.stderr.write(f"\r{done}" + " " * 12 + "\n")
            sys.stderr.flush()
        elif not args.quiet:
            print(done)
        if args.verbose and not args.quiet:
            print("  " + shlex.join(command))

    elapsed = time.monotonic() - started
    if not args.quiet:
        print(
            f"Done: {rendered} rendered, {skipped_count} skipped, "
            f"{failures} failed in {elapsed:.1f}s."
        )
    return 1 if failures else 0


def _print_dry_run(
    config: Config,
    items: list[PlanItem],
    *,
    dedupe: bool,
    quiet: bool,
) -> int:
    to_render = sum(1 for item in items if item.should_render)
    skipped = len(items) - to_render
    if not quiet:
        print(f"Config:  {config.path}")
        print(f"Output:  {config.output_dir}")
        print(f"Dedupe:  {'on' if dedupe else 'off'}")

    for position, item in enumerate(items, start=1):
        action = "RENDER" if item.should_render else "SKIP  "
        if not quiet:
            print(f"[{position}/{len(items)}] {action} {item.clip.name}: {item.reason}")

    if not quiet:
        print(f"Dry run: {to_render} to render, {skipped} to skip.")
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    config = load_config(
        args.config,
        output_dir=args.output_dir,
        ffmpeg=args.ffmpeg,
    )
    print(f"OK: {len(config.clips)} clip(s)")
    print(f"Output: {config.output_dir}")
    print(f"Dedupe: {'on' if config.dedupe else 'off'}")
    for position, clip in enumerate(config.clips, start=1):
        print(
            f"  {position}. {clip.name} <- {clip.source} "
            f"[{format_timecode(clip.start)} - {format_timecode(clip.end)}]"
        )
    return 0


def _run_init(args: argparse.Namespace) -> int:
    path = args.path.expanduser()
    if path.exists() and not args.force:
        print(f"error: {path} already exists (use --force)", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    print(f"Created {path}")
    return 0
