"""Choosing among candidates that share history."""

from apm.gitio.ancestry import best_candidate


def _probe(candidates, ok=True):
    return {"arcos_ok": ok, "arcos_sha": "a" * 40, "candidates": candidates}


def test_no_shared_history_returns_nothing():
    data = _probe([
        {"url": "https://github.com/lldpd/lldpd.git", "ref": "master",
         "reachable": True, "shared": False},
        {"url": "https://salsa.debian.org/debian/lldpd.git", "ref": "debian/master",
         "reachable": True, "shared": False},
    ])
    assert best_candidate(data) is None


def test_the_closest_shared_candidate_wins():
    """openssl trails master by far more than its own 3.0 branch."""
    data = _probe([
        {"url": "https://github.com/openssl/openssl.git", "ref": "master",
         "reachable": True, "shared": True, "behind": 18423, "arcos_only": 3452},
        {"url": "https://github.com/openssl/openssl.git", "ref": "openssl-3.0",
         "reachable": True, "shared": True, "behind": 10265, "arcos_only": 3452},
    ])
    assert best_candidate(data)["ref"] == "openssl-3.0"


def test_packaging_repo_can_win_over_the_project_repo():
    """ARCoS forked the Debian packaging repo for lttng-tools."""
    data = _probe([
        {"url": "https://github.com/lttng/lttng-tools.git", "ref": "master",
         "reachable": True, "shared": False},
        {"url": "https://salsa.debian.org/debian/ltt-control.git", "ref": "debian/sid",
         "reachable": True, "shared": True, "behind": 21, "arcos_only": 3},
    ])
    chosen = best_candidate(data)
    assert chosen["url"].endswith("ltt-control.git")
    assert chosen["behind"] == 21


def test_unfetchable_fork_returns_nothing():
    assert best_candidate(_probe([], ok=False)) is None
    assert best_candidate(None) is None


def test_project_repo_wins_over_packaging_repo_when_both_share_history():
    """iputils shares history with both; the project repo was offered first.

    Without repository priority, "fewest commits behind" reassigned bookworm to
    the project repo and trixie to the packaging repo - the same package with two
    different origins, decided by an accident of commit counts.
    """
    data = _probe([
        {"url": "https://github.com/iputils/iputils.git", "ref": "master",
         "reachable": True, "shared": True, "behind": 142, "arcos_only": 313},
        {"url": "https://salsa.debian.org/debian/iputils.git", "ref": "master",
         "reachable": True, "shared": True, "behind": 66, "arcos_only": 84},
    ])
    assert best_candidate(data)["url"] == "https://github.com/iputils/iputils.git"


def test_priority_survives_an_unreachable_first_candidate():
    data = _probe([
        {"url": "https://github.com/a/a.git", "ref": "master",
         "reachable": False, "shared": False},
        {"url": "https://github.com/a/a.git", "ref": "a-1.0",
         "reachable": True, "shared": True, "behind": 90, "arcos_only": 2},
        {"url": "https://salsa.debian.org/debian/a.git", "ref": "master",
         "reachable": True, "shared": True, "behind": 5, "arcos_only": 1},
    ])
    chosen = best_candidate(data)
    assert chosen["url"] == "https://github.com/a/a.git" and chosen["ref"] == "a-1.0"


def test_closest_ref_within_the_winning_repository():
    data = _probe([
        {"url": "https://github.com/openssl/openssl.git", "ref": "master",
         "reachable": True, "shared": True, "behind": 18423, "arcos_only": 3452},
        {"url": "https://github.com/openssl/openssl.git", "ref": "openssl-3.0",
         "reachable": True, "shared": True, "behind": 10265, "arcos_only": 3452},
        {"url": "https://salsa.debian.org/debian/openssl.git", "ref": "master",
         "reachable": True, "shared": True, "behind": 3, "arcos_only": 1},
    ])
    assert best_candidate(data)["ref"] == "openssl-3.0"


def test_a_fork_that_could_not_be_fetched_is_not_a_finding():
    """Distinct from "no shared history": one is a measurement, the other a miss.

    A transient SSH failure once cached itself as a permanent verdict for four
    rows, which read as "this package has no origin" rather than "ask again".
    """
    assert best_candidate({"arcos_ok": False, "candidates": []}) is None
