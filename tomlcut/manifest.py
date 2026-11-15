"""Persistent record of rendered clips, used for duplicate detection."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import MANIFEST_NAME

MANIFEST_VERSION = 1


class ManifestError(Exception):
    """Raised when the manifest cannot be read or written."""


@dataclass(frozen=True)
class Entry:
    key: str
    source: str
    source_size: int
    source_mtime_ns: int
    start: float
    end: float
    output: str
    rendered_at: str
    command: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source,
            "source_size": self.source_size,
            "source_mtime_ns": self.source_mtime_ns,
            "start": self.start,
            "end": self.end,
            "output": self.output,
            "rendered_at": self.rendered_at,
            "command": list(self.command),
        }

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> Entry:
        if not isinstance(raw, dict):
            raise ManifestError(f"entry {key!r} is not an object")

        stored_key = raw.get("key", key)
        if stored_key != key:
            raise ManifestError(f"entry {key!r} has a mismatched key")

        source = raw.get("source")
        output = raw.get("output")
        rendered_at = raw.get("rendered_at")
        if not isinstance(source, str) or not isinstance(output, str):
            raise ManifestError(f"entry {key!r} has an invalid source or output")
        if not isinstance(rendered_at, str):
            raise ManifestError(f"entry {key!r} has an invalid rendered_at")

        source_size = raw.get("source_size")
        source_mtime_ns = raw.get("source_mtime_ns")
        if (
            isinstance(source_size, bool)
            or not isinstance(source_size, int)
            or isinstance(source_mtime_ns, bool)
            or not isinstance(source_mtime_ns, int)
        ):
            raise ManifestError(f"entry {key!r} has an invalid source fingerprint")

        start = raw.get("start")
        end = raw.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
        ):
            raise ManifestError(f"entry {key!r} has an invalid time range")

        command = raw.get("command", [])
        if not isinstance(command, list) or any(
            not isinstance(item, str) for item in command
        ):
            raise ManifestError(f"entry {key!r} has an invalid command")

        return cls(
            key=key,
            source=source,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
            start=float(start),
            end=float(end),
            output=output,
            rendered_at=rendered_at,
            command=tuple(command),
        )


class Manifest:
    """The manifest file living inside the output directory."""

    def __init__(self, path: Path, entries: dict[str, Entry] | None = None) -> None:
        self.path = path
        self.entries: dict[str, Entry] = entries or {}

    @classmethod
    def load(cls, output_dir: Path) -> Manifest:
        path = output_dir / MANIFEST_NAME
        if not path.exists():
            return cls(path)

        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, UnicodeError) as exc:
            raise ManifestError(f"{path}: cannot read manifest: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ManifestError(
                f"{path}: invalid JSON ({exc}); delete the file to rebuild it"
            ) from exc

        if not isinstance(raw, dict):
            raise ManifestError(f"{path}: manifest root must be an object")
        if raw.get("version") != MANIFEST_VERSION:
            raise ManifestError(
                f"{path}: unsupported manifest version {raw.get('version')!r}"
            )
        renders = raw.get("renders")
        if not isinstance(renders, dict):
            raise ManifestError(f"{path}: 'renders' must be an object")

        entries: dict[str, Entry] = {}
        for key, value in renders.items():
            if not isinstance(key, str):
                raise ManifestError(f"{path}: render keys must be strings")
            try:
                entries[key] = Entry.from_dict(key, value)
            except ManifestError as exc:
                raise ManifestError(f"{path}: {exc}") from exc
        return cls(path, entries)

    def save(self) -> None:
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                prefix=f".{MANIFEST_NAME}.", suffix=".tmp", dir=parent
            )
        except OSError as exc:
            raise ManifestError(f"{self.path}: cannot write manifest: {exc}") from exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": MANIFEST_VERSION,
                        "renders": {
                            key: entry.to_dict()
                            for key, entry in sorted(self.entries.items())
                        },
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException as exc:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            if isinstance(exc, OSError):
                raise ManifestError(
                    f"{self.path}: cannot write manifest: {exc}"
                ) from exc
            raise

    def add(self, entry: Entry) -> None:
        """Record a render, replacing older records for the same output file."""

        self.entries = {
            key: existing
            for key, existing in self.entries.items()
            if existing.output != entry.output
        }
        self.entries[entry.key] = entry

    def find_by_output(self, output: str) -> Entry | None:
        for entry in self.entries.values():
            if entry.output == output:
                return entry
        return None
