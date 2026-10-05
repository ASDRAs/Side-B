from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request

from app.schemas.access import AccessDecisionBody, AccessRequestBody, ListableStatus
from app.services.access import (
    MAX_CURSOR_LENGTH,
    AccessIdentity,
    DecisionRequest,
    isoformat,
    validate_uid,
)
from app.services.auth import (
    AUTHORIZATION_ERRORS,
    AuthenticatedUser,
    access_store,
    authorize_access_request,
    authorize_admin_read,
    authorize_admin_write,
    http_error,
)

router = APIRouter(tags=["access"])

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 50


@router.post("/access/request")
async def request_access(
    request: Request,
    user: AuthenticatedUser = Depends(authorize_access_request),
    _body: Annotated[AccessRequestBody | None, Body()] = None,
):
    """Create the caller's own pending request.

    A repeat returns the stored state unchanged, so a rejected or blocked
    account cannot reopen itself by asking again.
    """
    try:
        result = await access_store(request).request_access(
            AccessIdentity(
                uid=user.uid, email=user.email, display_name=user.display_name
            )
        )
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc
    return {
        "access_status": result.record.status,
        "access_requested_at": isoformat(result.record.requested_at),
        "created": result.created,
    }


@router.get("/admin/access-users")
async def list_access_users(
    request: Request,
    _admin: AuthenticatedUser = Depends(authorize_admin_read),
    status: ListableStatus = "pending",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        str | None, Query(min_length=1, max_length=MAX_CURSOR_LENGTH)
    ] = None,
):
    try:
        page = await access_store(request).list_users(status, limit, cursor)
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc
    admins = request.app.state.admin_uids
    return {
        "status": status,
        "items": [
            {**record.public(), "is_admin": record.uid in admins}
            for record in page.records
        ],
        "next_cursor": page.next_cursor,
    }


# ``{uid:path}`` lets malformed IDs such as ``a/b`` or an empty segment reach
# validation and fail with 422 instead of silently routing elsewhere.
@router.post("/admin/access-users/{uid:path}/decision")
async def decide_access(
    uid: str,
    body: AccessDecisionBody,
    request: Request,
    admin: AuthenticatedUser = Depends(authorize_admin_write),
):
    try:
        result = await access_store(request).decide(
            DecisionRequest(
                actor_uid=admin.uid,
                target_uid=validate_uid(uid),
                action=body.action,
                expected_revision=body.expected_revision,
                operation_id=str(body.operation_id),
            )
        )
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc
    return result.public()
