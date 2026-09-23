"""Serve the generated mapping reports for download.

Read-only, and deliberately narrow: only files this tool generates, only from
the output directory, and only by a name matched against that listing. A path
supplied by a caller is never joined onto a directory here - that is how a
download endpoint turns into a way to read /etc/passwd.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ...config import ROOT
from ...domain.models import ReportFile
from ..deps import container

router = APIRouter(tags=["reports"])

OUT_DIR = Path(ROOT) / "out"

# What a generated report is allowed to be called and to contain.
_PATTERNS = ("upstream-mapping*.csv", "upstream-mapping*.xlsx",
             "upstream-md-plan-*.json", "upstream-md-plan-*.md")

_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    ".json": "application/json",
    ".md": "text/markdown",
}


def _listing() -> dict:
    found = {}
    for pattern in _PATTERNS:
        for path in sorted(OUT_DIR.glob(pattern)):
            if path.is_file():
                found[path.name] = path
    return found


@router.get("/reports", response_model=List[ReportFile])
def reports(app=Depends(container)):
    """Every generated report on disk, newest information first."""
    return [
        ReportFile(
            name=name,
            size_bytes=path.stat().st_size,
            modified_at=path.stat().st_mtime,
            release=_release_of(name),
            download_url=f"/api/reports/{name}",
        )
        for name, path in sorted(_listing().items())
    ]


@router.get("/reports/{name}")
def download(name: str, app=Depends(container)):
    path = _listing().get(name)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": (
                    f"No generated report named '{name}'. Run "
                    f"`python -m apm.resolve_bookworm` to produce one."
                ),
            },
        )
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
        filename=path.name,
    )


def _release_of(name: str) -> str:
    """The release a report is for, when its name says so."""
    stem = name.rsplit(".", 1)[0]
    for prefix in ("upstream-mapping-", "upstream-md-plan-"):
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return ""
