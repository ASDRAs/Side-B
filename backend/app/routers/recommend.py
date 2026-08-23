import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.schemas.recommend import RecommendRequest, RecommendResponse
from app.services.access import (
    BackendAccessConfigurationError,
    BackendAccessRateLimitError,
    BackendAccessUnauthorizedError,
)
from app.services.recommend_service import run_recommend

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/recommend",
    response_model=RecommendResponse,
    response_model_exclude_none=True,
)
async def recommend(
    req: RecommendRequest,
    request: Request,
    access_token: str | None = Header(default=None, alias="X-Side-B-Access-Token"),
):
    """
    유저의 query를 입력받아 추천결과를 return
    """
    access = request.app.state.recommend_access
    if access is not None:
        try:
            await access.authorize(access_token)
        except BackendAccessConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "recommend_access_configuration_error",
                    "message": "백엔드에 SIDE_B_ACCESS_TOKEN이 설정되지 않았습니다.",
                },
            ) from exc
        except BackendAccessUnauthorizedError as exc:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "recommend_unauthorized",
                    "message": "팀 백엔드 토큰이 올바르지 않습니다.",
                },
            ) from exc
        except BackendAccessRateLimitError as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "recommend_rate_limited",
                    "message": "추천 요청이 너무 많습니다.",
                },
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc

    http = request.app.state.http
    lastfm = request.app.state.lastfm_pylast
    settings = get_settings()

    logger.info("recommend request - query=%r, top_n=%d", req.query, req.top_n)
    result = await run_recommend(req.query, req.top_n, http, lastfm, settings)
    return RecommendResponse(**result)
