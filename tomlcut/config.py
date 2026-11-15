"""Loading and validation of TOML clip definitions."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .timecode import TimecodeError, parse_timecode

MANIFEST_NAME = ".tomlcut-manifest.json"

_TOP_LEVEL_KEYS = {"output_dir", "dedupe", "render", "clips", "clip"}
_RENDER_KEYS = {"mode", "ffmpeg", "preset", "crf", "audio_bitrate", "extra_args"}
_CLIP_KEYS = {"source", "start", "duration", "end", "name", "extra_args"}
_RENDER_MODES = {"encode", "copy"}


class ConfigError(Exception):
    """Raised when a TOML definition is missing or invalid."""


@dataclass(frozen=True)
class RenderOptions:
    mode: str = "encode"
    ffmpeg: str = "ffmpeg"
    preset: str = "medium"
    crf: int = 18
    audio_bitrate: str = "192k"
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Clip:
    index: int
    source: Path
    start: float
    end: float
    name: str
    extra_args: tuple[str, ...] = ()

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Config:
    path: Path
    output_dir: Path
    dedupe: bool
    render: RenderOptions
    clips: tuple[Clip, ...]

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / MANIFEST_NAME


def load_config(
    path: Path | str,
    *,
    output_dir: Path | str | None = None,
    ffmpeg: str | None = None,
) -> Config:
    """Load a TOML file and validate every clip definition."""

    config_path = Path(path).expanduser()
    try:
        config_path = config_path.resolve()
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"{config_path}: file not found") from exc
    except IsADirectoryError as exc:
        raise ConfigError(f"{config_path}: is a directory") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{config_path}: cannot read file: {exc}") from exc

    _check_keys(data, _TOP_LEVEL_KEYS, "top level")

    if output_dir is not None:
        try:
            resolved_output_dir = Path(output_dir).expanduser().resolve()
        except OSError as exc:
            raise ConfigError(f"output_dir: invalid path: {exc}") from exc
    elif "output_dir" in data:
        resolved_output_dir = _resolve_path(
            config_path.parent, data["output_dir"], "output_dir"
        )
    else:
        raise ConfigError(
            "output_dir is required (set it in the TOML file or pass --output-dir)"
        )

    if resolved_output_dir.exists() and not resolved_output_dir.is_dir():
        raise ConfigError(f"output_dir: {resolved_output_dir} is not a directory")

    dedupe = data.get("dedupe", True)
    if not isinstance(dedupe, bool):
        raise ConfigError("dedupe: expected true or false")

    render = _load_render_options(data.get("render", {}), ffmpeg=ffmpeg)

    if "clips" in data and "clip" in data:
        raise ConfigError("use either [[clips]] or [[clip]], not both")
    raw_clips = data.get("clips", data.get("clip"))
    if raw_clips is None:
        raise ConfigError("no clips defined (add at least one [[clips]] table)")
    if not isinstance(raw_clips, list):
        raise ConfigError("clips: expected an array of tables ([[clips]])")
    if not raw_clips:
        raise ConfigError("clips: at least one clip is required")

    clips: list[Clip] = []
    targets: dict[Path, Clip] = {}
    for index, raw_clip in enumerate(raw_clips):
        clip = _load_clip(index, raw_clip, config_path.parent, resolved_output_dir)
        target = (resolved_output_dir / clip.name).resolve()
        previous = targets.get(target)
        if previous is not None and not _same_clip_definition(previous, clip):
            raise ConfigError(
                f"clips[{previous.index}] and clips[{index}] both write to "
                f"{clip.name!r}; output names must be unique"
            )
        if target.exists() and target.is_dir():
            raise ConfigError(
                f"clips[{index}].name: {target} is a directory, not a file"
            )
        targets[target] = previous or clip
        clips.append(clip)

    return Config(
        path=config_path,
        output_dir=resolved_output_dir,
        dedupe=dedupe,
        render=render,
        clips=tuple(clips),
    )


def _load_render_options(raw: Any, *, ffmpeg: str | None) -> RenderOptions:
    if not isinstance(raw, dict):
        raise ConfigError("render: expected a table")
    _check_keys(raw, _RENDER_KEYS, "render")

    mode = raw.get("mode", "encode")
    if mode not in _RENDER_MODES:
        modes = ", ".join(sorted(_RENDER_MODES))
        raise ConfigError(f"render.mode: expected one of {modes}")

    ffmpeg_name = ffmpeg if ffmpeg is not None else raw.get("ffmpeg", "ffmpeg")
    if not isinstance(ffmpeg_name, str) or not ffmpeg_name.strip():
        raise ConfigError("render.ffmpeg: expected a non-empty string")

    preset = raw.get("preset", "medium")
    if not isinstance(preset, str) or not preset.strip():
        raise ConfigError("render.preset: expected a non-empty string")

    crf = raw.get("crf", 18)
    if isinstance(crf, bool) or not isinstance(crf, int):
        raise ConfigError("render.crf: expected an integer")
    if not 0 <= crf <= 63:
        raise ConfigError("render.crf: expected a value between 0 and 63")

    audio_bitrate = raw.get("audio_bitrate", "192k")
    if not isinstance(audio_bitrate, str) or not audio_bitrate.strip():
        raise ConfigError("render.audio_bitrate: expected a non-empty string")

    extra_args = _string_list(raw.get("extra_args", []), "render.extra_args")

    return RenderOptions(
        mode=mode,
        ffmpeg=ffmpeg_name,
        preset=preset,
        crf=crf,
        audio_bitrate=audio_bitrate,
        extra_args=extra_args,
    )


def _load_clip(
    index: int,
    raw: Any,
    config_dir: Path,
    output_dir: Path,
) -> Clip:
    where = f"clips[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a table")
    _check_keys(raw, _CLIP_KEYS, where)

    if "source" not in raw:
        raise ConfigError(f"{where}.source: required")
    source = _resolve_path(config_dir, raw["source"], f"{where}.source")
    if not source.exists():
        raise ConfigError(f"{where}.source: file not found: {source}")
    if not source.is_file():
        raise ConfigError(f"{where}.source: not a regular file: {source}")

    if "name" not in raw:
        raise ConfigError(f"{where}.name: required")
    name = _validate_output_name(raw["name"], output_dir, f"{where}.name")

    start = _parse_time(raw.get("start", 0), f"{where}.start")

    if "duration" in raw and "end" in raw:
        raise ConfigError(f"{where}: use either duration or end, not both")
    if "duration" in raw:
        duration = _parse_time(raw["duration"], f"{where}.duration")
        if duration <= 0:
            raise ConfigError(f"{where}.duration: must be greater than zero")
        end = start + duration
    elif "end" in raw:
        end = _parse_time(raw["end"], f"{where}.end")
        if end <= start:
            raise ConfigError(f"{where}.end: must be greater than start")
    else:
        raise ConfigError(f"{where}: duration or end is required")
    if not math.isfinite(end):
        raise ConfigError(f"{where}: end time must be finite")

    extra_args = _string_list(raw.get("extra_args", []), f"{where}.extra_args")

    return Clip(
        index=index,
        source=source,
        start=start,
        end=end,
        name=name,
        extra_args=extra_args,
    )


def _parse_time(value: Any, field: str) -> float:
    try:
        return parse_timecode(value, field=field)
    except TimecodeError as exc:
        raise ConfigError(str(exc)) from exc


def _validate_output_name(value: Any, output_dir: Path, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field}: expected a non-empty string")
    if "\x00" in value:
        raise ConfigError(f"{field}: must not contain NUL bytes")

    relative = Path(value)
    if relative.is_absolute():
        raise ConfigError(f"{field}: must be relative to output_dir")
    if any(part == ".." for part in relative.parts):
        raise ConfigError(f"{field}: must not contain '..'")
    if not relative.suffix:
        raise ConfigError(f"{field}: must include a file extension")
    if relative.name in {"", ".", ".."}:
        raise ConfigError(f"{field}: invalid file name")

    target = (output_dir / relative).resolve()
    try:
        target.relative_to(output_dir)
    except ValueError as exc:
        raise ConfigError(f"{field}: path escapes output_dir") from exc
    if target == output_dir / MANIFEST_NAME:
        raise ConfigError(f"{field}: {MANIFEST_NAME} is reserved")

    return relative.as_posix()


def _resolve_path(base: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field}: expected a non-empty path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve()
    except OSError as exc:
        raise ConfigError(f"{field}: invalid path: {exc}") from exc


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{field}: expected an array of strings")
    return tuple(value)


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigError(f"{where}: unknown key(s): {joined}")


def _same_clip_definition(left: Clip, right: Clip) -> bool:
    return (
        left.source == right.source
        and left.start == right.start
        and left.end == right.end
        and left.extra_args == right.extra_args
    )
