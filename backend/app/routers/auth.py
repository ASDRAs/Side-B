from fastapi import APIRouter, Header, HTTPException, Request

from app.services.auth import (
    AuthenticationConfigurationError,
    AuthenticationDeniedError,
    AuthenticationError,
    AuthenticationUnavailableError,
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
    try:
        user = await request.app.state.auth_service.authenticate(
            feature="recommend",
            authorization=authorization,
            legacy_token=legacy_token,
        )
    except AuthenticationConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_configuration_error", "message": str(exc)},
        ) from exc
    except AuthenticationUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "auth_verification_unavailable",
                "message": "로그인 확인 서비스를 일시적으로 사용할 수 없습니다.",
            },
        ) from exc
    except AuthenticationDeniedError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "auth_account_denied", "message": str(exc)},
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "auth_unauthorized", "message": str(exc)},
        ) from exc

    return {
        "uid": user.uid,
        "email": user.email,
        "display_name": user.display_name,
    }
