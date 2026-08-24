import asyncio
from typing import NoReturn

from fastapi import APIRouter, Header, HTTPException, Request

from app.schemas.youtube_export import (
    YouTubeMatchedTrack,
    YouTubeMatchRequest,
    YouTubeMatchResponse,
    YouTubeTrackRequest,
    YouTubeUnmatchedTrack,
)
from app.services.access import (
    BackendAccessConfigurationError,
    BackendAccessRateLimitError,
    BackendAccessUnauthorizedError,
)
from app.services.youtube import (
    YouTubeAPIUnavailableError,
    YouTubeConfigurationError,
    YouTubeQuotaExceededError,
)
from app.utils.text import compact_text

router = APIRouter(prefix="/exports/youtube", tags=["youtube-export"])


def _service_unavailable(code: str, message: str) -> NoReturn:
    raise HTTPException(status_code=503, detail={"code": code, "message": message})


async def _match_all(matcher, tracks: list[tuple[int, YouTubeTrackRequest]]):
    tasks = [
        asyncio.create_task(matcher.match_track(track.name, track.artist))
        for _, track in tracks
    ]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


@router.post("/matches", response_model=YouTubeMatchResponse)
async def match_youtube_tracks(
    req: YouTubeMatchRequest,
    request: Request,
    export_token: str | None = Header(default=None, alias="X-Side-B-Export-Token"),
):
    try:
        await request.app.state.youtube_export_access.authorize(export_token)
    except BackendAccessConfigurationError:
        _service_unavailable(
            "youtube_export_configuration_error",
            "백엔드에 SIDE_B_ACCESS_TOKEN이 설정되지 않았습니다.",
        )
    except BackendAccessUnauthorizedError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "youtube_export_unauthorized",
                "message": "YouTube 내보내기 토큰이 올바르지 않습니다.",
            },
        ) from exc
    except BackendAccessRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "youtube_export_rate_limited",
                "message": "YouTube 내보내기 요청이 너무 많습니다.",
            },
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    matcher = request.app.state.youtube_matcher
    unique: list[tuple[int, YouTubeTrackRequest]] = []
    seen: set[tuple[str, str]] = set()
    for position, track in enumerate(req.tracks):
        key = (compact_text(track.artist), compact_text(track.name))
        if key in seen:
            continue
        seen.add(key)
        unique.append((position, track))

    try:
        outcomes = await _match_all(matcher, unique)
    except YouTubeConfigurationError:
        _service_unavailable(
            "youtube_configuration_error",
            "백엔드에 YOUTUBE_API_KEY가 설정되지 않았습니다.",
        )
    except YouTubeQuotaExceededError:
        _service_unavailable(
            "youtube_quota_exceeded", "YouTube 검색 할당량이 소진되었습니다."
        )
    except YouTubeAPIUnavailableError:
        _service_unavailable(
            "youtube_api_unavailable",
            "YouTube 검색 API를 일시적으로 사용할 수 없습니다.",
        )

    matched = []
    unmatched = []
    seen_video_ids: set[str] = set()
    for (position, track), outcome in zip(unique, outcomes, strict=True):
        if outcome.match:
            if outcome.match.video_id in seen_video_ids:
                unmatched.append(
                    YouTubeUnmatchedTrack(
                        name=track.name,
                        artist=track.artist,
                        reason="duplicate_video",
                        position=position,
                    )
                )
                continue
            seen_video_ids.add(outcome.match.video_id)
            matched.append(
                YouTubeMatchedTrack(
                    name=track.name,
                    artist=track.artist,
                    video_id=outcome.match.video_id,
                    youtube_title=outcome.match.youtube_title,
                    channel_title=outcome.match.channel_title,
                    confidence=outcome.match.confidence,
                    auto_selected=outcome.reason != "low_confidence",
                    position=position,
                )
            )
        else:
            unmatched.append(
                YouTubeUnmatchedTrack(
                    name=track.name,
                    artist=track.artist,
                    reason=outcome.reason or "not_found",
                    position=position,
                )
            )

    return YouTubeMatchResponse(
        bucket=req.bucket,
        requested=len(req.tracks),
        matched=matched,
        unmatched=unmatched,
        deduplicated=len(req.tracks) - len(unique),
    )
