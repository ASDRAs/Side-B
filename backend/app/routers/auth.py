from fastapi import APIRouter, Header, Request

from app.services.access import isoformat
from app.services.auth import (
    AUTHORIZATION_ERRORS,
    access_store,
    authenticate_identity,
    http_error,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
async def auth_config(request: Request):
    return request.app.state.auth_service.config


@router.get("/me")
async def auth_me(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    legacy_token: str | None = Header(default=None, alias="X-Side-B-Access-Token"),
):
    """Return the verified identity and, separately, its approval state.

    With the Firestore store, an unapproved account is still a valid login:
    this returns 200 with ``access_status`` so the extension can keep the
    session and offer the request/status screen. Only identity failures are
    401/403. Environment-allowlist mode keeps its historical 403 for accounts
    outside the allowlist.
    """
    try:
        store = access_store(request)
        user = await authenticate_identity(
            request,
            feature="access_status",
            authorization=authorization,
            legacy_token=legacy_token,
            # Unchanged legacy semantics: /auth/me behaves like /recommend.
            auth_feature="recommend",
        )
        record = await store.get(user.uid) if store is not None else None
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc

    if store is None:
        access_status = "approved"
    else:
        access_status = record.status if record is not None else "unregistered"
    return {
        "uid": user.uid,
        "email": user.email,
        "display_name": user.display_name,
        "access_status": access_status,
        "access_requested_at": isoformat(record.requested_at) if record else None,
        "access_store": "firestore" if store is not None else "env",
        # A display hint only. Every administration call re-checks the server
        # administrator list.
        "can_manage_access": bool(
            store is not None and user.uid in request.app.state.admin_uids
        ),
    }
