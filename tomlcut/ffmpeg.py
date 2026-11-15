"""FFmpeg command construction and execution."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config import Clip, Config, RenderOptions
from .timecode import format_ffmpeg_time

ProgressCallback = Callable[[float], None]

_MP4_FAMILY = {".mp4", ".m4v", ".mov"}


class FFmpegError(RuntimeError):
    """Raised when FFmpeg is unavailable or a render fails."""


def check_ffmpeg(binary: str) -> str:
    """Verify that the configured FFmpeg binary can be executed."""

    try:
        result = subprocess.run(
            [binary, "-version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FFmpegError(
            f"FFmpeg not found: {binary!r}. Install FFmpeg or set render.ffmpeg / --ffmpeg."
        ) from exc
    except OSError as exc:
        raise FFmpegError(f"cannot execute FFmpeg {binary!r}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise FFmpegError(
            f"FFmpeg {binary!r} exited with status {result.returncode}: {detail}"
        )
    return (result.stdout or "").splitlines()[0] if result.stdout else binary


def build_command(clip: Clip, render: RenderOptions, output: Path) -> list[str]:
    """Build the FFmpeg invocation for one clip."""

    command = [
        render.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-ss",
        format_ffmpeg_time(clip.start),
        "-i",
        str(clip.source),
        "-t",
        format_ffmpeg_time(clip.duration),
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
    ]

    suffix = output.suffix.lower()
    if render.mode == "copy":
        command += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    elif suffix == ".webm":
        command += [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            str(render.crf),
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "libopus",
            "-b:a",
            render.audio_bitrate,
        ]
    else:
        command += [
            "-c:v",
            "libx264",
            "-preset",
            render.preset,
            "-crf",
            str(render.crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            render.audio_bitrate,
        ]
        if suffix in _MP4_FAMILY:
            command += ["-movflags", "+faststart"]

    command += list(render.extra_args)
    command += list(clip.extra_args)
    command.append(str(output))
    return command


def render_clip(
    config: Config,
    clip: Clip,
    target: Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, ...]:
    """Render one clip to a temporary file and atomically move it into place."""

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}.",
            suffix=f".part{target.suffix}",
            dir=target.parent,
        )
        os.close(descriptor)
        os.unlink(temporary_name)
    except OSError as exc:
        raise FFmpegError(f"cannot create temporary file for {target}: {exc}") from exc

    temporary = Path(temporary_name)
    command = build_command(clip, config.render, temporary)

    try:
        if on_progress is not None:
            on_progress(0.0)
        _run_ffmpeg(command, clip.duration, on_progress)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise FFmpegError(f"FFmpeg produced no output for {target.name}")
        os.replace(temporary, target)
        if on_progress is not None:
            on_progress(1.0)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise

    display_command = tuple(
        str(target) if part == str(temporary) else part for part in command
    )
    return display_command


def _run_ffmpeg(
    command: list[str],
    duration: float,
    on_progress: ProgressCallback | None,
) -> None:
    with tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise FFmpegError(
                f"FFmpeg not found: {command[0]!r}. "
                "Install FFmpeg or set render.ffmpeg / --ffmpeg."
            ) from exc
        except OSError as exc:
            raise FFmpegError(f"cannot execute FFmpeg: {exc}") from exc

        try:
            assert process.stdout is not None
            for line in process.stdout:
                if on_progress is not None:
                    _report_progress(line, duration, on_progress)
            returncode = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()

        if returncode != 0:
            stderr_file.seek(0)
            detail = stderr_file.read().decode("utf-8", errors="replace").strip()
            if len(detail) > 4000:
                detail = "..." + detail[-4000:]
            message = f"FFmpeg exited with status {returncode}"
            if detail:
                message += f":\n{detail}"
            raise FFmpegError(message)


def _report_progress(
    line: str,
    duration: float,
    on_progress: ProgressCallback,
) -> None:
    text = line.strip()
    if not text:
        return
    if text == "progress=end":
        on_progress(1.0)
        return
    key, separator, value = text.partition("=")
    if not separator or key not in {"out_time_us", "out_time_ms"}:
        return
    try:
        microseconds = int(value)
    except ValueError:
        return
    if microseconds < 0:
        return
    on_progress(min(microseconds / 1_000_000 / duration, 1.0))
