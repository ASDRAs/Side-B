import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas.genre_classification import (
    GenreClassificationRequest,
    GenreClassificationResponse,
)
from app.services.auth import AuthenticatedUser, authorize_genre
from app.services.genre_classification_service import (
    GenreClassificationConfigurationError,
    run_genre_classification,
)
from app.services.inference_client import (
    InferenceAudioError,
    InferenceTimeoutError,
    InferenceUnavailableError,
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
    _user: AuthenticatedUser = Depends(authorize_genre),
) -> GenreClassificationResponse:
    try:
        result = await asyncio.wait_for(
            run_genre_classification(
                track_name=req.track_name,
                artist=req.artist,
                http=request.app.state.http,
                settings=request.app.state.settings,
                inference=request.app.state.genre_inference,
            ),
            # Preview lookup plus a scale-to-zero model cold start.
            timeout=150,
        )

    except GenreClassificationConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "genre_configuration_error",
                "message": "장르 분류 서비스 설정이 완료되지 않았습니다.",
            },
        ) from exc

    except (InferenceTimeoutError, TimeoutError) as exc:
        raise HTTPException(
            504,
            detail={
                "code": "genre_timeout",
                "message": "장르 분석 응답 시간이 초과되었습니다.",
            },
        ) from exc

    except InferenceUnavailableError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "genre_unavailable",
                "message": "장르 분석 서비스를 사용할 수 없습니다.",
            },
        ) from exc

    except InferenceAudioError as exc:
        raise HTTPException(
            422,
            detail={
                "code": "genre_audio_invalid",
                "message": "미리듣기 음원을 분석할 수 없습니다.",
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
        model_version=result.model_version,
    )
