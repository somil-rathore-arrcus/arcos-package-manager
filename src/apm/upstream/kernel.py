"""Upstream selection for the Linux kernel.

The kernel is not tracked by a Debian source version the way a normal package
is: ARCoS follows a linux-stable maintenance series. The series is derived from
the Debian kernel version for the release (6.1.187-1 -> linux-6.1.y), never
assumed, because pairing a fork with the wrong series silently produces an
enormous and meaningless "behind" count rather than an error.
"""

from __future__ import annotations

import re
from typing import Optional

_VERSION = re.compile(r"^(\d+)\.(\d+)")


def series_for_version(debian_version: str) -> Optional[str]:
    """6.1.187-1 -> linux-6.1.y"""
    version = (debian_version or "").split(":", 1)[-1]
    match = _VERSION.match(version)
    if not match:
        return None
    return f"linux-{match.group(1)}.{match.group(2)}.y"
