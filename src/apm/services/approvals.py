"""Human approvals that the tool may not grant itself.

A content match between an ARCoS tree and an upstream release tag is a finding;
treating it as the fork's base is a decision, and only a person makes it. The
decision is recorded here with its provenance - who, when, by what method,
against which tag and commit, with what score - and the resolver reads it back.

Runtime state, so it lives in the writable output directory (APM_APPROVALS_FILE
overrides the location), never in config/, which is read-only in a container.
A decision worth keeping permanently can be copied into config/overrides.yaml
as a `content_base:` block, which takes precedence.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

APPROVALS_FILE_ENV = "APM_APPROVALS_FILE"
REQUIRED = ("repository", "base_tag", "base_sha", "method", "verified_by")


def approvals_file(root: Path) -> Path:
    configured = os.environ.get(APPROVALS_FILE_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path(root) / "out" / "approvals.yaml"


class ApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        with self.path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data if isinstance(data, dict) else {}

    def get(self, package: str, release: str) -> Optional[dict]:
        record = ((self._load().get("content_base") or {}).get(package) or {}).get(release)
        if not record:
            return None
        record = dict(record)
        record.setdefault("approval_source", str(self.path.name))
        return record

    def approve(self, package: str, release: str, record: dict) -> dict:
        missing = [k for k in REQUIRED if not record.get(k)]
        if missing:
            raise ValueError(f"an approval needs {', '.join(missing)}")
        entry = dict(record)
        entry.setdefault(
            "verified_at",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
        data = self._load()
        data.setdefault("version", 1)
        data.setdefault("content_base", {}).setdefault(package, {})[release] = entry
        self._write(data)
        return entry

    def revoke(self, package: str, release: str) -> bool:
        data = self._load()
        releases = (data.get("content_base") or {}).get(package) or {}
        if release not in releases:
            return False
        del releases[release]
        self._write(data)
        return True

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "# Human approvals recorded by the ARCoS Package Manager.\n"
            "# Each content_base entry makes a fork that shares no git history\n"
            "# comparable against the named tag. Delete an entry to revoke it.\n"
            + yaml.safe_dump(data, sort_keys=True, default_flow_style=False)
        )
        handle, temp = tempfile.mkstemp(dir=str(self.path.parent),
                                        prefix=f".{self.path.name}.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise
