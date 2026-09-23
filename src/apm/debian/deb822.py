"""Parser for the deb822 stanza format used by Debian index files.

Fields may continue onto following lines, which start with a space or tab. A
continuation line is part of the previous field's value, so a line reading
"Package: foo" inside a long Description must not be mistaken for a new field -
the naive line-wise regex that does this is a real source of wrong metadata.
"""

from __future__ import annotations

from typing import Iterator


def parse_stanzas(text: str) -> Iterator[dict]:
    """Yield each stanza as {field: value}. Field names keep their original case."""
    fields = {}
    key = None
    for raw in text.splitlines():
        if not raw.strip():
            if fields:
                yield fields
                fields, key = {}, None
            continue
        if raw[0] in " \t":
            # Continuation of the previous field.
            if key is not None:
                fields[key] = fields[key] + "\n" + raw.strip()
            continue
        if ":" not in raw:
            continue
        name, _, value = raw.partition(":")
        key = name.strip()
        fields[key] = value.strip()
    if fields:
        yield fields
