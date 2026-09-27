"""Convert an Office document to one PDF so MinerU can parse it as a single file."""

import os
import shutil
import subprocess
from pathlib import Path

from common.logger import get_logger

logger = get_logger(__name__)

_CONVERT_TIMEOUT_SECONDS = 300

# Each Office family uses its own PDF export filter.
_WRITER_FILTER = "pdf:writer_pdf_Export"
_CALC_FILTER = "pdf:calc_pdf_Export"
_IMPRESS_FILTER = "pdf:impress_pdf_Export"

_PDF_EXPORT_FILTERS = {
    ".doc": _WRITER_FILTER,
    ".docx": _WRITER_FILTER,
    ".xls": _CALC_FILTER,
    ".xlsx": _CALC_FILTER,
    ".ppt": _IMPRESS_FILTER,
    ".pptx": _IMPRESS_FILTER,
}


OFFICE_SUFFIXES = tuple(_PDF_EXPORT_FILTERS)


def is_office_document(path: Path) -> bool:
    """Whether LibreOffice can turn this path into a PDF with a known filter."""
    return path.suffix.lower() in _PDF_EXPORT_FILTERS


def _soffice_executable() -> Path:
    """Return the LibreOffice binary that waits for the conversion to finish."""
    names = ["soffice.com", "soffice"] if os.name == "nt" else ["soffice"]
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    if os.name == "nt":
        candidates = [
            Path(r"C:\Program Files\LibreOffice\program\soffice.com"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.com"),
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    raise ValueError(
        "LibreOffice (soffice) was not found. Install LibreOffice and ensure "
        "soffice is on PATH so Office files can be converted to PDF."
    )


def office_to_pdf(source: Path, out_dir: Path) -> Path:
    """Convert a Word, Excel, or PowerPoint file to a PDF in ``out_dir``.

    Uses a private LibreOffice profile so concurrent conversions do not share
    the default user installation lock.
    """
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Office file not found: {source}")
    export_filter = _PDF_EXPORT_FILTERS.get(source.suffix.lower())
    if export_filter is None:
        raise ValueError(f"Unsupported Office file type: {source.suffix}")
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = out_dir / "lo-profile"
    profile.mkdir(exist_ok=True)
    soffice = _soffice_executable()
    logger.info(f"Converting {source.name} to PDF with LibreOffice ({export_filter})")
    try:
        completed = subprocess.run(
            [
                str(soffice),
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--nologo",
                "--convert-to",
                export_filter,
                "--outdir",
                str(out_dir),
                str(source),
            ],
            cwd=out_dir,
            capture_output=True,
            timeout=_CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"LibreOffice timed out converting {source.name} to PDF"
        ) from exc
    expected = out_dir / f"{source.stem}.pdf"
    if expected.is_file():
        return expected
    pdfs = [path for path in out_dir.glob("*.pdf") if path.is_file()]
    if len(pdfs) == 1:
        return pdfs[0]
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise ValueError(
        f"LibreOffice did not produce a PDF for {source.name}"
        + (f": {detail}" if detail else "")
    )
