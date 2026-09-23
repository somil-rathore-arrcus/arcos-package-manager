"""The stanza parser must not be fooled by field-looking lines inside a value."""

from pathlib import Path

from apm.debian.deb822 import parse_stanzas

DATA = Path(__file__).parent / "data"


def test_continuation_lines_are_not_new_fields():
    stanzas = list(parse_stanzas((DATA / "sources.snippet").read_text()))
    assert len(stanzas) == 2

    pyrad = stanzas[0]
    assert pyrad["Package"] == "pyrad"
    # "Package: not-a-real-field" is indented, so it belongs to Description.
    assert pyrad["Version"] == "2.1-3"
    assert "not-a-real-field" in pyrad["Description"]
    assert pyrad["Vcs-Git"] == "https://salsa.debian.org/python-team/packages/pyrad.git"


def test_second_stanza_is_separate():
    stanzas = list(parse_stanzas((DATA / "sources.snippet").read_text()))
    assert stanzas[1]["Package"] == "lldpd"
    assert stanzas[1]["Directory"] == "pool/main/l/lldpd"


def test_empty_input():
    assert list(parse_stanzas("")) == []
