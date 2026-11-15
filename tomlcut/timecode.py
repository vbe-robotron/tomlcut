"""Parsing and formatting of time values.

Time values in a TOML file may be either numbers (seconds) or strings in one of
the following formats:

    12.5        seconds
    01:23.5     minutes:seconds
    01:02:03.5  hours:minutes:seconds
"""

from __future__ import annotations

import math
import re
from typing import Any

_SECONDS_RE = re.compile(r"^(?P<seconds>\d+(?:\.\d+)?)$")
_COLON_SECONDS_RE = re.compile(r"^(?P<seconds>\d{1,2}(?:\.\d+)?)$")
_INTEGER_RE = re.compile(r"^\d+$")


class TimecodeError(ValueError):
    """Raised when a time value cannot be parsed."""


def parse_timecode(value: Any, *, field: str = "time") -> float:
    """Convert a TOML time value into a non-negative number of seconds."""

    if isinstance(value, bool):
        raise TimecodeError(
            f"{field}: expected seconds or a time string, got a boolean"
        )

    if isinstance(value, (int, float)):
        seconds = float(value)
        if not math.isfinite(seconds):
            raise TimecodeError(f"{field}: time must be finite")
        if seconds < 0:
            raise TimecodeError(f"{field}: time must not be negative")
        return seconds

    if not isinstance(value, str):
        raise TimecodeError(f"{field}: expected seconds or a time string")

    text = value.strip()
    if not text:
        raise TimecodeError(f"{field}: time must not be empty")

    parts = text.split(":")
    if len(parts) > 3:
        raise TimecodeError(f"{field}: invalid time {value!r}")

    if len(parts) == 1:
        match = _SECONDS_RE.fullmatch(parts[0])
        if not match:
            raise TimecodeError(f"{field}: invalid time {value!r}")
        return float(match.group("seconds"))

    try:
        if len(parts) == 2:
            minutes_text, seconds_text = parts
            if not _INTEGER_RE.fullmatch(minutes_text):
                raise ValueError
            seconds_match = _COLON_SECONDS_RE.fullmatch(seconds_text)
            if not seconds_match:
                raise ValueError
            minutes = int(minutes_text)
            seconds = float(seconds_match.group("seconds"))
            if seconds >= 60:
                raise ValueError
            return minutes * 60 + seconds

        hours_text, minutes_text, seconds_text = parts
        if not _INTEGER_RE.fullmatch(hours_text) or not _INTEGER_RE.fullmatch(
            minutes_text
        ):
            raise ValueError
        seconds_match = _COLON_SECONDS_RE.fullmatch(seconds_text)
        if not seconds_match:
            raise ValueError
        hours = int(hours_text)
        minutes = int(minutes_text)
        seconds = float(seconds_match.group("seconds"))
        if minutes >= 60 or seconds >= 60:
            raise ValueError
        return hours * 3600 + minutes * 60 + seconds
    except ValueError as exc:
        raise TimecodeError(f"{field}: invalid time {value!r}") from exc


def format_timecode(seconds: float) -> str:
    """Format seconds as ``MM:SS.mmm`` or ``HH:MM:SS.mmm`` for humans."""

    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)

    text = f"{minutes:02d}:{whole_seconds:02d}"
    if milliseconds:
        text += f".{milliseconds:03d}".rstrip("0")
    if hours:
        text = f"{hours:02d}:{text}"
    return text


def format_ffmpeg_time(seconds: float) -> str:
    """Format seconds as a decimal string suitable for FFmpeg arguments."""

    text = f"{seconds:.6f}".rstrip("0").rstrip(".")
    return text or "0"
