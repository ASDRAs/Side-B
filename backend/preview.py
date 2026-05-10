"""
preview.py
──────────────────────────────────────────────────────────────────
Deezer 30초 미리 듣기 라우터

엔드포인트
  GET /preview        → { preview_url, deezer_id, track, artist }
  GET /preview/stream → 오디오 바이트 스트리밍 (CORS 우회용)
"""

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["preview"])

_DEEZER_SEARCH = "https://api.deezer.com/search"


# ── 내부 유틸 ────────────────────────────────────────────────────

async def _fetch_preview(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str,
) -> tuple[str | None, str | None]:
    """Deezer 검색으로 (preview_url, deezer_id) 를 반환한다.

    1차: track:"name" artist:"artist" 구조화 쿼리
    2차: "name artist" 자유 텍스트
    3차: 곡명만
    preview 필드가 빈 문자열인 트랙은 건너뛰고 다음 결과를 탐색한다.
    """
    clean = re.sub(r"\(.*?\)|\[.*?\]", "", track_name).strip()

    queries: list[str] = [
        f'track:"{clean}" artist:"{artist}"' if artist else f'track:"{clean}"',
        f"{clean} {artist}".strip(),
        clean,
    ]

    for query in queries:
        try:
            resp = await http.get(_DEEZER_SEARCH, params={"q": query}, timeout=8.0)
        except Exception as exc:
            logger.warning("[Preview] Deezer 요청 실패 (%s): %s", type(exc).__name__, exc)
            continue

        if resp.status_code == 429:
            logger.warning("[Preview] Deezer 429 — 미리 듣기 불가")
            return None, None

        for item in resp.json().get("data", []):
            preview = item.get("preview") or ""
            if preview:
                deezer_id = str(item.get("id", ""))
                logger.info("[Preview] 발견: %s - %s (id=%s)", track_name, artist, deezer_id)
                return preview, deezer_id

    logger.info("[Preview] 미리 듣기 없음: %s - %s", track_name, artist)
    return None, None


# ── GET /preview ─────────────────────────────────────────────────

@router.get("/preview")
async def get_preview_url(
    request: Request,
    track: str = Query(..., min_length=1, max_length=200, description="곡명"),
    artist: str = Query(default="", max_length=200, description="아티스트명 (선택)"),
):
    """Deezer 30초 미리 듣기 URL을 반환합니다.

    클라이언트가 직접 URL을 재생할 때 사용하세요.
    URL은 Deezer CDN 만료 시각이 포함되어 있으므로 즉시 재생해야 합니다.
    """
    http: httpx.AsyncClient = request.app.state.http
    preview_url, deezer_id = await _fetch_preview(http, track, artist)

    if not preview_url:
        raise HTTPException(
            status_code=404,
            detail=f"'{track}'의 미리 듣기를 찾을 수 없습니다.",
        )

    return {
        "preview_url": preview_url,
        "deezer_id": deezer_id,
        "track": track,
        "artist": artist,
    }


# ── GET /preview/stream ──────────────────────────────────────────

@router.get("/preview/stream")
async def stream_preview(
    request: Request,
    track: str = Query(..., min_length=1, max_length=200, description="곡명"),
    artist: str = Query(default="", max_length=200, description="아티스트명 (선택)"),
):
    """Deezer 30초 오디오를 서버 경유로 스트리밍합니다.

    웹 브라우저 환경에서 Deezer CDN CORS 차단이 발생할 경우 사용하세요.
    CORS 제약이 없는 클라이언트라면 /preview 에서 받은 URL을 직접 재생하는 것이
    더 효율적입니다.
    """
    http: httpx.AsyncClient = request.app.state.http
    preview_url, _ = await _fetch_preview(http, track, artist)

    if not preview_url:
        raise HTTPException(
            status_code=404,
            detail=f"'{track}'의 미리 듣기를 찾을 수 없습니다.",
        )

    async def _generate():
        # 공유 클라이언트가 아닌 별도 스트림 클라이언트 사용
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as stream_client:
            async with stream_client.stream("GET", preview_url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=8192):
                    yield chunk

    return StreamingResponse(
        _generate(),
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Content-Disposition": f'inline; filename="{track}.mp3"',
        },
    )
