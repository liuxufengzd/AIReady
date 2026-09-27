import time
from collections.abc import Iterator
from pathlib import Path

import dagster as dg

from dataprep.common.const import SUPPORTED_FILE_TYPES

_SUPPORTED_SUFFIXES = frozenset(SUPPORTED_FILE_TYPES)
_DEFAULT_STORE_ROOT = Path(__file__).resolve().parents[4] / "store" / "s3"


class LocalDocStore(dg.ConfigurableResource):
    """Local filesystem store for raw files and extraction output.

    ``{root}/raw/{project}/{filename}`` is the landing file.
    ``{root}/processed/{project}/{filename}/`` is the accepted result.
    ``{root}/processed/{project}/{filename}/tmp/`` holds work before review.
    Only supported, non-empty files that have stopped changing are yielded.
    """

    root: str = str(_DEFAULT_STORE_ROOT)
    stable_after_seconds: float = 2

    def raw_file(self, project: str, filename: str) -> Path:
        return self._under(
            "raw",
            self._component(project, "project"),
            self._component(filename, "filename"),
        )

    def processed_dir(self, project: str, filename: str) -> Path:
        """Directory that currently holds the extraction output."""
        return self._under(
            "processed",
            self._component(project, "project"),
            self._component(filename, "filename"),
        )

    def working_dir(self, project: str, filename: str) -> Path:
        """Temporary working directory removed after review."""
        return self.processed_dir(project, filename) / "tmp"

    def iter_raw_files(self) -> Iterator[tuple[str, str, int]]:
        """Yield ``(project, filename, mtime_ns)`` for files ready to parse."""
        raw_root = Path(self.root) / "raw"
        if not raw_root.is_dir():
            return

        now = time.time()
        for project_dir in sorted(path for path in raw_root.iterdir() if path.is_dir()):
            for path in sorted(
                child for child in project_dir.iterdir() if child.is_file()
            ):
                if not self._is_ready(path, now):
                    continue
                yield project_dir.name, path.name, path.stat().st_mtime_ns

    def _is_ready(self, path: Path, now: float) -> bool:
        if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            return False
        stat = path.stat()
        if stat.st_size <= 0:
            return False

        # Avoid parsing files that are not stable yet(e.g. still writing).
        if (
            self.stable_after_seconds > 0
            and now - stat.st_mtime < self.stable_after_seconds
        ):
            return False
        return True

    def _under(self, *parts: str) -> Path:
        base = Path(self.root).resolve()
        target = base.joinpath(*parts).resolve()

        # Avoid path traversal attacks by checking if the target is under the base.
        if not target.is_relative_to(base):
            raise ValueError(f"Path escapes the document store: {target}")
        return target

    def _component(self, value: str, label: str) -> str:
        """Reject names that would escape ``raw`` or ``processed``."""
        if not value or value in {".", ".."} or value != Path(value).name:
            raise ValueError(f"Invalid {label}: {value!r}")
        return value
