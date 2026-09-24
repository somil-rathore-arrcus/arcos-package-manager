"""Turn service failures into responses that say what went wrong.

Every service raises an error carrying an ErrorCode, and each maps to a status
the frontend can render differently: a missing branch is not the same problem as
a missing token, and a UI that shows "request failed" for both is useless.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from ..domain.enums import ErrorCode
from ..services.comparison_service import ComparisonError
from ..services.github_service import GitHubError
from ..services.patch_service import PatchError
from ..services.upstream_md_publisher import PublishError
from ..services.upstream_service import UpstreamServiceError

log = logging.getLogger(__name__)

STATUS = {
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.REPOSITORY_UNAVAILABLE: 502,
    ErrorCode.BRANCH_NOT_FOUND: 404,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.CONFLICT: 409,
    ErrorCode.NO_COMMON_ANCESTOR: 422,
    ErrorCode.UPSTREAM_NOT_RESOLVED: 409,
    ErrorCode.GIT_ERROR: 502,
    ErrorCode.INTERNAL: 500,
}

HANDLED = (
    ComparisonError, PatchError, GitHubError, UpstreamServiceError, PublishError,
)


def install(app) -> None:
    for exception_type in HANDLED:
        app.add_exception_handler(exception_type, _handle)


async def _handle(request: Request, exc: Exception) -> JSONResponse:
    code = getattr(exc, "code", ErrorCode.INTERNAL)
    detail = getattr(exc, "detail", "") or None
    status = STATUS.get(code, 500)
    if status >= 500:
        log.error("%s on %s: %s", code.value, request.url.path, exc)
    else:
        log.info("%s on %s: %s", code.value, request.url.path, exc)
    return JSONResponse(
        status_code=status,
        content={"code": code.value, "message": str(exc), "detail": detail},
    )
