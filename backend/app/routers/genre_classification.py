import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.schemas.genre_classification import (
    GenreClassificationRequest,
    GenreClassificationResponse,
)
from app.services.access import (
    BackendAccessConfigurationError,
    BackendAccessRateLimitError,
    BackendAccessUnauthorizedError,
)
from app.services.genre_classification_service import (
    GenreClassificationConfigurationError,
    run_genre_classification,
)
from app.utils.preview_audio import PreviewNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/genre-classification",
    tags=["genre-classification"],
)


@router.post(
    "",
    response_model=GenreClassificationResponse,
)
async def classify_genre(
    req: GenreClassificationRequest,
    request: Request,
    access_token: str | None = Header(
        default=None,
        alias="X-Side-B-Access-Token",
    ),
) -> GenreClassificationResponse:
    # 기존 recommend API와 인증 및 rate limit 공유
    access = request.app.state.recommend_access

    if access is not None:
        try:
            await access.authorize(access_token)

        except BackendAccessConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "genre_access_configuration_error",
                    "message": "백엔드 access token이 설정되지 않았습니다.",
                },
            ) from exc

        except BackendAccessUnauthorizedError as exc:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "genre_unauthorized",
                    "message": "팀 백엔드 토큰이 올바르지 않습니다.",
                },
            ) from exc

        except BackendAccessRateLimitError as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "genre_rate_limited",
                    "message": "장르 분류 요청이 너무 많습니다.",
                },
                headers={
                    "Retry-After": str(exc.retry_after),
                },
            ) from exc

    try:
        result = await run_genre_classification(
            track_name=req.track_name,
            artist=req.artist,
            http=request.app.state.http,
            settings=request.app.state.settings,
            models=request.app.state.genre_models,
        )

    except GenreClassificationConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "genre_configuration_error",
                "message": "장르 분류 서비스 설정이 완료되지 않았습니다.",
            },
        ) from exc

    except PreviewNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "preview_not_found",
                "message": "분류에 사용할 preview 음원을 찾지 못했습니다.",
            },
        ) from exc

    except Exception as exc:
        logger.exception(
            "Genre classification failed: %s - %s",
            req.artist,
            req.track_name,
        )

        raise HTTPException(
            status_code=500,
            detail={
                "code": "genre_classification_failed",
                "message": "장르 분류 중 오류가 발생했습니다.",
            },
        ) from exc

    return GenreClassificationResponse(
        track_name=result.track_name,
        artist=result.artist,
        genre=result.genre,
        score=result.score,
    )
