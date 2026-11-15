"""Deciding which clips need to be rendered."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Clip, Config, RenderOptions
from .manifest import Manifest
from .timecode import format_ffmpeg_time

KEY_VERSION = 1


class PlanError(Exception):
    """Raised when a render plan cannot be built."""


@dataclass(frozen=True)
class SourceFingerprint:
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class PlanItem:
    clip: Clip
    source: SourceFingerprint
    target: Path
    key: str
    action: Literal["render", "skip"]
    reason: str
    duplicate_of: int | None = None

    @property
    def should_render(self) -> bool:
        return self.action == "render"


def fingerprint(clip: Clip) -> SourceFingerprint:
    try:
        info = clip.source.stat()
    except OSError as exc:
        raise PlanError(f"{clip.source}: cannot read source: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PlanError(f"{clip.source}: source is not a regular file")
    return SourceFingerprint(
        path=clip.source,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
    )


def render_key(clip: Clip, render: RenderOptions, source: SourceFingerprint) -> str:
    """Build a stable identity for one rendered file.

    The key covers everything that affects the output: the source file and its
    fingerprint, the selected range, the output name, and the encoder settings.
    """

    payload: dict[str, object] = {
        "key_version": KEY_VERSION,
        "source": str(source.path),
        "source_size": source.size,
        "source_mtime_ns": source.mtime_ns,
        "start": format_ffmpeg_time(clip.start),
        "end": format_ffmpeg_time(clip.end),
        "output": clip.name,
        "mode": render.mode,
        "ffmpeg": render.ffmpeg,
        "render_extra_args": list(render.extra_args),
        "clip_extra_args": list(clip.extra_args),
    }
    if render.mode == "encode":
        payload["preset"] = render.preset
        payload["crf"] = render.crf
        payload["audio_bitrate"] = render.audio_bitrate

    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_plan(config: Config, manifest: Manifest, *, dedupe: bool) -> list[PlanItem]:
    if config.output_dir.exists() and not config.output_dir.is_dir():
        raise PlanError(f"{config.output_dir}: output_dir is not a directory")

    items: list[PlanItem] = []
    seen: dict[str, PlanItem] = {}

    for clip in config.clips:
        source = fingerprint(clip)
        key = render_key(clip, config.render, source)
        target = config.output_dir / clip.name

        if target.exists() and target.is_dir():
            raise PlanError(f"{target}: output path is a directory")

        action: Literal["render", "skip"] = "render"
        reason = "not rendered yet"
        duplicate_of: int | None = None

        if not dedupe:
            reason = "re-render (dedupe disabled)" if target.exists() else "new clip"
        elif manifest.entries.get(key) is not None and _is_complete(target):
            action = "skip"
            reason = "already rendered"
        elif key in seen:
            action = "skip"
            reason = "duplicate clip definition"
            duplicate_of = seen[key].clip.index
        elif _is_complete(target):
            previous = manifest.find_by_output(clip.name)
            if previous is not None and previous.key != key:
                reason = "source or render settings changed"
            else:
                action = "skip"
                reason = "output file already exists (not in manifest)"
        elif manifest.entries.get(key) is not None:
            reason = "previous output is missing, rendering again"
        else:
            reason = "new clip"

        item = PlanItem(
            clip=clip,
            source=source,
            target=target,
            key=key,
            action=action,
            reason=reason,
            duplicate_of=duplicate_of,
        )
        items.append(item)
        seen.setdefault(key, item)

    return items


def _is_complete(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
