"""
preview.py
──────────────────────────────────────────────────────────────────
Deezer 30초 미리 듣기 라우터

엔드포인트
  GET /preview        → { preview_url, deezer_id, track, artist, requested }
  GET /preview/stream → 오디오 바이트 스트리밍 (CORS 우회용)
"""

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.services.catalog import _alias_artist_score, _looks_like_bad_version
from app.utils.text import compact_text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["preview"])

_DEEZER_SEARCH = "https://api.deezer.com/search"

# 후보를 채택하는 최소 기준. fixture와 실 Deezer 응답으로 검증한 정책값이다.
# 아티스트 0.8은 _artist_ratio 기준이다. 정상 표기는 구분자 분리로 1.0을 받고
# 오답은 0.62 이하라(TAEMIN 대 TAEYEON = 0.615) 그 사이를 끊는다.
_ARTIST_MIN_SCORE = 0.8
# 제목에서는 부분문자열 특례를 쓰지 않는다. `If` 요청에 `I`가, `Shape of You`
# 요청에 긴 리믹스 제목이 합격했던 실제 오탐을 막기 위한 하한이다.
_TITLE_MIN_SCORE = 0.8

# 같은 곡의 다른 버전을 구분하는 표기. 요청에 있으면 후보에도 있어야 한다.
_VERSION_MARKER_PATTERNS = (
    ("acoustic", re.compile(r"\bacoustic\b", re.I)),
    ("live", re.compile(r"\blive\b", re.I)),
    ("unplugged", re.compile(r"\bunplugged\b", re.I)),
    ("remaster", re.compile(r"\bremaster(?:ed)?\b", re.I)),
    ("remix", re.compile(r"\bremix(?:ed)?\b", re.I)),
    ("demo", re.compile(r"\bdemo\b", re.I)),
    ("radio edit", re.compile(r"\bradio[\s-]+edit\b", re.I)),
    ("piano", re.compile(r"\bpiano\b", re.I)),
)

_BRACKETED_TEXT = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_VERSION_SUFFIX = re.compile(r"\s+[-–—]\s+(.+?)\s*$")


@dataclass(frozen=True)
class PreviewMatch:
    preview_url: str
    deezer_id: str
    track: str
    artist: str


class PreviewProviderUnavailable(Exception):
    """Deezer가 일시적으로 검색 요청을 처리할 수 없는 상태."""

    def __init__(self, *, retry_after: str | None = None):
        super().__init__("Deezer preview provider unavailable")
        self.retry_after = retry_after


# ── 내부 유틸 ────────────────────────────────────────────────────


def _strip_version(title: str) -> str:
    """버전 표기가 들어 있는 괄호만 제거하고 제목의 일부인 괄호는 보존한다."""

    def replace_if_version(match: re.Match[str]) -> str:
        content = match.group(1) or match.group(2) or ""
        return " " if _markers_in_text(content) else match.group(0)

    cleaned = _BRACKETED_TEXT.sub(replace_if_version, title)
    suffix = _VERSION_SUFFIX.search(cleaned)
    if suffix and _markers_in_text(suffix.group(1)):
        cleaned = cleaned[: suffix.start()]
    return re.sub(r"\s+", " ", cleaned).strip()


def _markers_in_text(value: str) -> frozenset[str]:
    return frozenset(
        marker for marker, pattern in _VERSION_MARKER_PATTERNS if pattern.search(value)
    )


def _version_markers(title: str) -> frozenset[str]:
    markers: set[str] = set()
    for match in _BRACKETED_TEXT.finditer(title):
        markers.update(_markers_in_text(match.group(1) or match.group(2) or ""))
    suffix = _VERSION_SUFFIX.search(title)
    if suffix:
        markers.update(_markers_in_text(suffix.group(1)))
    return frozenset(markers)


def _title_ratio(candidate: str, expected: str) -> float:
    """제목 전용 비교. 짧은 부분문자열을 같은 곡으로 취급하지 않는다."""
    left = compact_text(_strip_version(candidate))
    right = compact_text(expected)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left), len(right)) <= 4:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _search_queries(track_name: str, artist: str) -> list[str]:
    """요청 버전을 보존한 검색을 먼저 하고, 기본 제목 검색은 fallback으로 둔다."""
    requested = track_name.strip()
    base = _strip_version(requested)
    titles = list(dict.fromkeys(title for title in (requested, base) if title))
    queries: list[str] = []
    for title in titles:
        escaped_title = title.replace('"', r'\"')
        escaped_artist = artist.replace('"', r'\"')
        queries.append(
            f'track:"{escaped_title}" artist:"{escaped_artist}"'
            if artist
            else f'track:"{escaped_title}"'
        )
        queries.append(f"{title} {artist}".strip())
    queries.append(base)
    return list(dict.fromkeys(query for query in queries if query))


def _content_disposition(track_name: str) -> str:
    """브라우저가 한글·특수문자 곡명을 안전하게 해석할 파일명 헤더를 만든다."""
    encoded_filename = quote(f"{track_name}.mp3", safe="")
    return (
        'inline; filename="preview.mp3"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


def _retry_after_seconds(value: str | None, *, default: int) -> str:
    """공급자 Retry-After를 안전한 delta-seconds 형식으로 정규화한다."""
    if value:
        candidate = value.strip()
        if candidate.isdecimal():
            return candidate
    return str(default)


def _provider_unavailable_http_error(
    exc: PreviewProviderUnavailable,
) -> HTTPException:
    headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
    return HTTPException(
        status_code=503,
        detail="미리 듣기 공급자가 일시적으로 응답할 수 없습니다.",
        headers=headers,
    )


async def _fetch_preview(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str,
) -> PreviewMatch | None:
    """Deezer 검색으로 요청한 곡과 일치하는 후보를 고른다.

    요청 제목·버전을 보존한 구조화/자유 텍스트 검색을 먼저 실행한다. 그 뒤
    버전 표기를 뺀 기본 제목으로 재시도하고, 마지막에 곡명만 검색한다.

    preview가 없는 트랙, 카라오케·커버판, 아티스트가 다른 트랙, 요청한 버전
    조건을 잃은 트랙은 후보에서 뺀다. 남는 후보가 없으면 None을 돌려주고
    호출부가 404로 처리한다. 공급자 제한·장애는 PreviewProviderUnavailable로
    분리한다. 예전에는 preview만 있으면 첫 결과를 그대로 썼고, 그래서
    `밤편지`/`IU` 요청에 Flow Music의 피아노 커버가 나갔다.
    """
    clean = _strip_version(track_name)
    wanted = _version_markers(track_name)

    queries = _search_queries(track_name, artist)

    for query in queries:
        try:
            resp = await http.get(_DEEZER_SEARCH, params={"q": query}, timeout=8.0)
        except httpx.RequestError as exc:
            logger.warning(
                "[Preview] Deezer 요청 실패 (%s): %s", type(exc).__name__, exc
            )
            raise PreviewProviderUnavailable(retry_after="30") from exc

        if resp.status_code == 429:
            logger.warning("[Preview] Deezer 429 — 미리 듣기 불가")
            raise PreviewProviderUnavailable(
                retry_after=_retry_after_seconds(
                    resp.headers.get("Retry-After"),
                    default=60,
                )
            )
        if resp.status_code >= 500:
            logger.warning("[Preview] Deezer HTTP %s: %s", resp.status_code, query)
            raise PreviewProviderUnavailable(
                retry_after=_retry_after_seconds(
                    resp.headers.get("Retry-After"),
                    default=30,
                )
            )
        if resp.status_code >= 400:
            logger.warning("[Preview] Deezer HTTP %s: %s", resp.status_code, query)
            continue

        try:
            payload = resp.json()
        except (TypeError, ValueError) as exc:
            logger.warning("[Preview] Deezer 응답 JSON 파싱 실패: %s", query)
            raise PreviewProviderUnavailable(retry_after="30") from exc
        items = payload.get("data", []) if isinstance(payload, dict) else []

        best = _best_candidate(items, clean, artist, wanted)
        if best:
            logger.info(
                "[Preview] 발견: %s - %s (id=%s)", best.track, best.artist, best.deezer_id
            )
            return best

    logger.info("[Preview] 일치하는 미리 듣기 없음: %s - %s", track_name, artist)
    return None


def _best_candidate(
    items: object,
    clean_title: str,
    artist: str,
    wanted: frozenset[str],
) -> PreviewMatch | None:
    if not isinstance(items, list):
        return None

    best: tuple[float, PreviewMatch] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        preview = item.get("preview") or ""
        if not preview:
            continue

        title = str(item.get("title") or "")
        artist_payload = item.get("artist")
        item_artist = str(
            artist_payload.get("name")
            if isinstance(artist_payload, dict) and artist_payload.get("name")
            else ""
        )
        if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
            continue
        if artist and _alias_artist_score(item_artist, (artist,)) < _ARTIST_MIN_SCORE:
            continue

        # 요청한 버전은 반드시 보존하고, 요청하지 않은 다른 버전도 허용하지
        # 않는다. 일반 `Shape of You` 요청에 리믹스를 재생하면 오답이다.
        markers = _version_markers(title)
        if markers != wanted:
            continue

        score = _title_ratio(title, clean_title)
        if score < _TITLE_MIN_SCORE:
            continue

        if best is None or score > best[0]:
            best = (
                score,
                PreviewMatch(
                    preview_url=preview,
                    deezer_id=str(item.get("id", "")),
                    track=title,
                    artist=item_artist,
                ),
            )

    return best[1] if best else None


# ── GET /preview ─────────────────────────────────────────────────


@router.get("/preview")
async def get_preview_url(
    request: Request,
    track: str = Query(..., min_length=1, max_length=200, description="곡명"),
    artist: str = Query(default="", max_length=200, description="아티스트명 (선택)"),
):
    """검증된 Deezer 30초 미리 듣기 정보를 반환합니다.

    클라이언트가 직접 URL을 재생할 때 사용하세요.
    URL은 Deezer CDN 만료 시각이 포함되어 있으므로 즉시 재생해야 합니다.
    track·artist는 실제로 선택된 Deezer 트랙 값이고, requested에는 클라이언트가
    보낸 원래 track·artist가 들어갑니다.
    """
    http: httpx.AsyncClient = request.app.state.http
    try:
        match = await _fetch_preview(http, track, artist)
    except PreviewProviderUnavailable as exc:
        raise _provider_unavailable_http_error(exc) from exc

    if not match:
        raise HTTPException(
            status_code=404,
            detail=f"'{track}'의 미리 듣기를 찾을 수 없습니다.",
        )

    # track·artist는 요청값 echo가 아니라 실제로 고른 Deezer 트랙이다.
    # 어떤 곡이 재생되는지 클라이언트가 확인할 수 있어야 한다.
    return {
        "preview_url": match.preview_url,
        "deezer_id": match.deezer_id,
        "track": match.track,
        "artist": match.artist,
        "requested": {"track": track, "artist": artist},
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
    try:
        match = await _fetch_preview(http, track, artist)
    except PreviewProviderUnavailable as exc:
        raise _provider_unavailable_http_error(exc) from exc

    if not match:
        raise HTTPException(
            status_code=404,
            detail=f"'{track}'의 미리 듣기를 찾을 수 없습니다.",
        )

    async def _generate():
        # 공유 클라이언트가 아닌 별도 스트림 클라이언트 사용
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as stream_client:
            async with stream_client.stream("GET", match.preview_url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=8192):
                    yield chunk

    return StreamingResponse(
        _generate(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": _content_disposition(match.track),
        },
    )
