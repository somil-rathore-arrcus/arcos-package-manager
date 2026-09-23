"""Dependency wiring for the API."""

from __future__ import annotations

from ..services.container import Container, get_container


def container() -> Container:
    return get_container()
