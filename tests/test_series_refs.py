"""Version-series branch matching must not match a longer number.

Both of these were real, silent mis-selections: bookworm ships ethtool 6.1 and a
substring match chose the 6.14 series; ifupdown2 3.0 matched a branch named after
pull request 230.
"""

import re

import pytest


def series_pattern(major, minor):
    return re.compile(r"(?<!\d)%s[._]%s(?!\d)" % (major, minor))


@pytest.mark.parametrize("major,minor,branch,matches", [
    ("6", "1", "ethtool-6.1.y", True),
    ("6", "1", "ethtool-6.14.y", False),
    ("6", "1", "v6.1", True),
    ("6", "12", "linux-6.12.y", True),
    ("6", "12", "linux-6.1.y", False),
    ("3", "0", "openssl-3.0", True),
    ("3", "0", "revert-230-dad_handling", False),
    ("3", "0", "openssl-3.0-stable", True),
    ("3", "0", "openssl-3.05", False),
    ("0", "13", "stable-0.13", True),
    ("1", "5", "stable-1.5", True),
    ("1", "5", "stable-1.55", False),
])
def test_series_matching(major, minor, branch, matches):
    assert bool(series_pattern(major, minor).search(branch)) is matches
