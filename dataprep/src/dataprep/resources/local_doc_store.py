from pathlib import Path

import dagster as dg

from common.published import store_root

_DEFAULT_STORE_ROOT = store_root()


class LocalDocStore(dg.ConfigurableResource):
    """Local filesystem store for raw files and extraction output.

    ``{root}/raw/{project}/{filename}`` is the landing file.
    ``{root}/processed/{project}/{filename}/`` is the accepted result.
    ``{root}/processed/{project}/{filename}/tmp/`` holds work before review.
    """

    root: str = str(_DEFAULT_STORE_ROOT)

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
