"""Dependency wiring for the API."""

from __future__ import annotations

import hmac

from fastapi import Request

from ..domain.enums import ErrorCode
from ..services.container import Container, get_container


def container() -> Container:
    return get_container()


class ApiAuthError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = ErrorCode.API_TOKEN_REQUIRED
        self.detail = ""


def require_write_access(http: Request, app) -> None:
    """Refuse a request that writes unless it carries this deployment's token.

    Writes are a push, a pull request, a cherry-pick that pushes, an approval
    and a file written on the server. Without APM_API_TOKEN every request is
    let through as before, and /api/health reports that it is.
    """
    expected = getattr(app, "api_token", "") or ""
    if not expected:
        return
    scheme, _, supplied = http.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode("utf-8"), expected.encode("utf-8")):
        return
    raise ApiAuthError(
        "This deployment requires its API token for actions that push, open a "
        "pull request or record an approval. Enter it on the dashboard, or send "
        "it as Authorization: Bearer <token>."
    )
