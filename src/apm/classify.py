"""Decide what kind of package this is.

Category answers "what is it", Status answers "did we confirm its upstream".
Keeping them separate is the point: a vendor blob with no upstream is a correct,
finished result, and reporting it as UNRESOLVED - as a single status column
forces you to - buries the two packages that are genuinely unresolved among
twenty-two that never had an upstream to find.
"""

from __future__ import annotations

from typing import Optional

from .models import Category, DebianSource


def classify(
    package_name: str,
    source: Optional[DebianSource],
    has_upstream: bool,
    settings,
    curated: bool = False,
) -> Category:
    if source is not None:
        return Category.DEBIAN_UPSTREAM if has_upstream else Category.DEBIAN_NO_UPSTREAM

    # Not in Debian at all.
    if settings.is_vendor(package_name):
        return Category.VENDOR
    if has_upstream or curated:
        # Not a Debian package, but a real external project ARCoS forked.
        return Category.THIRD_PARTY
    return Category.ARRCUS_NATIVE
