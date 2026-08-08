"""
preview.py
──────────────────────────────────────────────────────────────────
30초 미리 듣기 라우터

엔드포인트
  GET /preview        → MediaBinding + requested
  GET /preview/stream → 오디오 바이트 스트리밍 (CORS 우회용)

공급자는 iTunes 우선, Deezer fallback이다. Deezer 단독으로는 한국어권
카탈로그를 다 덮지 못한다 — `Through the Night / IU`는 Deezer 검색에서
확정되지 않고 동명이곡만 나오지만 iTunes에는 있다. 반대로 `기다리다 / Younha`는
Deezer에 정상 등록돼 있다. 아티스트 단위로 갈리므로 한쪽으로 고정하지 않는다.
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx
from async_lru import alru_cache
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.services.catalog import _alias_artist_score, _looks_like_bad_version
from app.utils.text import compact_text

# iTunes 회로차단기와 아트워크 정규화는 추천 경로와 상태를 공유해야 한다.
# 각자 따로 두면 한쪽이 받은 429를 다른 쪽이 모른다.
from recommend_algo.common.sources import (
    _is_itunes_rate_limited,
    _itunes_artwork,
    _mark_itunes_rate_limited,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["preview"])

_DEEZER_SEARCH = "https://api.deezer.com/search"
_ITUNES_SEARCH = "https://itunes.apple.com/search"

# 공급자별 미리 듣기 미디어 형식. 실측 기준 iTunes는 audio/x-m4p(m4a),
# Deezer는 audio/mpeg(mp3)다.
# ponytail: 고정 매핑. 상류 Content-Type을 쓰려면 /preview 응답 전에 스트림을
# 열어야 해서 느려진다. 형식이 갈리기 시작하면 그때 헤더를 읽는다.
_PROVIDER_MEDIA: dict[str, tuple[str, str]] = {
    "itunes": ("audio/x-m4p", "m4a"),
    "deezer": ("audio/mpeg", "mp3"),
}

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
class MediaBinding:
    """재생 가능한 미디어. 어느 공급자가 줬는지까지 들고 다닌다.

    `supports_range`는 두지 않는다. 지금 프록시는 클라이언트 Range를 상류로
    전달하지 않아서 아무 데서도 소비되지 않는 죽은 필드가 된다. Range를 실제로
    지원할 때 함께 넣는다.
    """

    provider: str
    provider_track_id: str
    preview_url: str
    content_type: str
    file_extension: str
    resolved_title: str
    resolved_artist: str
    artwork_url: str | None = None


class PreviewProviderUnavailable(Exception):
    """미리 듣기 공급자가 일시적으로 요청을 처리할 수 없는 상태."""

    def __init__(self, *, retry_after: str | None = None):
        super().__init__("preview provider unavailable")
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


def _requested_titles(track_name: str) -> list[str]:
    """요청 표기를 먼저, 버전 표기를 뺀 기본 제목을 그 다음에 둔다."""
    requested = track_name.strip()
    return list(dict.fromkeys(t for t in (requested, _strip_version(requested)) if t))


def _search_queries(track_name: str, artist: str) -> list[str]:
    """Deezer 검색어. 요청 버전 보존 검색이 먼저고 기본 제목이 fallback이다."""
    queries: list[str] = []
    for title in _requested_titles(track_name):
        escaped_title = title.replace('"', r'\"')
        escaped_artist = artist.replace('"', r'\"')
        queries.append(
            f'track:"{escaped_title}" artist:"{escaped_artist}"'
            if artist
            else f'track:"{escaped_title}"'
        )
        queries.append(f"{title} {artist}".strip())
    queries.append(_strip_version(track_name.strip()))
    return list(dict.fromkeys(query for query in queries if query))


def _itunes_terms(track_name: str, artist: str) -> list[str]:
    """iTunes 검색어. 구조화 문법이 없어 자유 텍스트 term만 쓴다."""
    terms = [f"{title} {artist}".strip() for title in _requested_titles(track_name)]
    return list(dict.fromkeys(term for term in terms if term))


def _content_disposition(track_name: str, file_extension: str) -> str:
    """브라우저가 한글·특수문자 곡명을 안전하게 해석할 파일명 헤더를 만든다.

    확장자는 공급자마다 다르다. iTunes 미리 듣기는 m4a라 .mp3로 내려보내면
    파일을 저장했을 때 형식이 어긋난다.
    """
    encoded_filename = quote(f"{track_name}.{file_extension}", safe="")
    return (
        f'inline; filename="preview.{file_extension}"; '
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


async def _fetch_itunes_preview(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str,
) -> MediaBinding | None:
    """iTunes 검색으로 미리 듣기를 찾는다. 실패는 조용히 Deezer로 넘긴다.

    iTunes는 fallback이 남아 있는 1순위 공급자라, 여기서 429나 장애가 나도
    503으로 끊지 않는다. Deezer가 같은 곡을 가지고 있을 수 있기 때문이다.
    429는 추천 경로와 공유하는 회로차단기에 기록해 반복 호출을 막는다.
    """
    if _is_itunes_rate_limited():
        logger.info("[Preview] iTunes 회로차단 중 — Deezer로 넘어간다")
        return None

    clean = _strip_version(track_name)
    wanted = _version_markers(track_name)

    for term in _itunes_terms(track_name, artist):
        try:
            resp = await http.get(
                _ITUNES_SEARCH,
                params={"term": term, "entity": "song", "limit": 10},
                timeout=5.0,
            )
        except httpx.RequestError as exc:
            logger.warning("[Preview] iTunes 요청 실패: %s", exc)
            return None

        if resp.status_code == 429:
            retry_after = _retry_after_seconds(
                resp.headers.get("Retry-After"), default=60
            )
            logger.warning("[Preview] iTunes 429 — 회로차단 후 Deezer로 넘어간다")
            _mark_itunes_rate_limited(int(retry_after))
            return None
        if resp.status_code >= 400:
            logger.warning("[Preview] iTunes HTTP %s: %s", resp.status_code, term)
            continue

        try:
            payload = resp.json()
        except (TypeError, ValueError):
            logger.warning("[Preview] iTunes 응답 JSON 파싱 실패: %s", term)
            return None
        results = payload.get("results", []) if isinstance(payload, dict) else []

        best = _best_itunes_candidate(results, clean, artist, wanted)
        if best:
            return best

    return None


async def _fetch_deezer_preview(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str,
) -> MediaBinding | None:
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
            return best

    return None


# ponytail: 확정된 binding과 CDN preview URL을 같은 항목으로 캐시한다. TTL은
# CDN 만료보다 짧게 잡아 만료된 URL이 나가지 않게 한다. 둘을 분리 저장하려면
# 프로세스 밖 캐시가 필요하므로 그때 나눈다.
@alru_cache(maxsize=256, ttl=600)
async def _resolve_media(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str,
) -> MediaBinding | None:
    """iTunes를 먼저 보고, 없으면 Deezer로 넘어간다.

    Deezer는 마지막 공급자라 여기서 나는 429·장애는 그대로 503으로 올린다.
    alru_cache는 예외를 캐시하지 않으므로 일시 장애가 고착되지 않는다.
    """
    binding = await _fetch_itunes_preview(http, track_name, artist)
    if binding is None:
        binding = await _fetch_deezer_preview(http, track_name, artist)

    if binding is None:
        logger.info("[Preview] 일치하는 미리 듣기 없음: %s - %s", track_name, artist)
        return None

    logger.info(
        "[Preview] 발견: %s - %s (%s:%s)",
        binding.resolved_title,
        binding.resolved_artist,
        binding.provider,
        binding.provider_track_id,
    )
    return binding


def _deezer_candidates(items: object) -> Iterator[MediaBinding]:
    """Deezer 검색 응답을 공급자 중립 형태로 바꾼다."""
    if not isinstance(items, list):
        return
    content_type, extension = _PROVIDER_MEDIA["deezer"]
    for item in items:
        if not isinstance(item, dict):
            continue
        preview = str(item.get("preview") or "")
        track_id = str(item.get("id") or "")
        # ID 없는 후보는 쓰지 않는다. 재생 단계에서 다시 문자열로 찾게 된다.
        if not preview or not track_id:
            continue
        artist_payload = item.get("artist")
        album = item.get("album")
        album = album if isinstance(album, dict) else {}
        yield MediaBinding(
            provider="deezer",
            provider_track_id=track_id,
            preview_url=preview,
            content_type=content_type,
            file_extension=extension,
            resolved_title=str(item.get("title") or ""),
            resolved_artist=str(
                artist_payload.get("name")
                if isinstance(artist_payload, dict) and artist_payload.get("name")
                else ""
            ),
            artwork_url=album.get("cover_big")
            or album.get("cover_medium")
            or album.get("cover"),
        )


def _itunes_candidates(results: object) -> Iterator[MediaBinding]:
    """iTunes 검색 응답을 공급자 중립 형태로 바꾼다."""
    if not isinstance(results, list):
        return
    content_type, extension = _PROVIDER_MEDIA["itunes"]
    for item in results:
        if not isinstance(item, dict):
            continue
        preview = str(item.get("previewUrl") or "")
        track_id = str(item.get("trackId") or "")
        if not preview or not track_id:
            continue
        yield MediaBinding(
            provider="itunes",
            provider_track_id=track_id,
            preview_url=preview,
            content_type=content_type,
            file_extension=extension,
            resolved_title=str(item.get("trackName") or ""),
            resolved_artist=str(item.get("artistName") or ""),
            artwork_url=_itunes_artwork(item),
        )


def _select_best(
    candidates: Iterator[MediaBinding],
    clean_title: str,
    artist: str,
    wanted: frozenset[str],
) -> MediaBinding | None:
    """후보 채택 정책. 공급자와 무관하게 이 함수 하나만 판단한다."""
    best: tuple[float, MediaBinding] | None = None
    for candidate in candidates:
        title = candidate.resolved_title
        item_artist = candidate.resolved_artist
        if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
            continue
        if artist and _alias_artist_score(item_artist, (artist,)) < _ARTIST_MIN_SCORE:
            continue

        # 요청한 버전은 반드시 보존하고, 요청하지 않은 다른 버전도 허용하지
        # 않는다. 일반 `Shape of You` 요청에 리믹스를 재생하면 오답이다.
        if _version_markers(title) != wanted:
            continue

        score = _title_ratio(title, clean_title)
        if score < _TITLE_MIN_SCORE:
            continue

        if best is None or score > best[0]:
            best = (score, candidate)

    return best[1] if best else None


def _best_candidate(
    items: object,
    clean_title: str,
    artist: str,
    wanted: frozenset[str],
) -> MediaBinding | None:
    """Deezer 검색 응답에서 고른다."""
    return _select_best(_deezer_candidates(items), clean_title, artist, wanted)


def _best_itunes_candidate(
    results: object,
    clean_title: str,
    artist: str,
    wanted: frozenset[str],
) -> MediaBinding | None:
    """iTunes 검색 응답에서 고른다. 채택 기준은 Deezer와 같다."""
    return _select_best(_itunes_candidates(results), clean_title, artist, wanted)


# ── GET /preview ─────────────────────────────────────────────────


@router.get("/preview")
async def get_preview_url(
    request: Request,
    track: str = Query(..., min_length=1, max_length=200, description="곡명"),
    artist: str = Query(default="", max_length=200, description="아티스트명 (선택)"),
):
    """검증된 30초 미리 듣기 정보를 반환합니다.

    클라이언트가 직접 URL을 재생할 때 사용하세요.
    URL에는 공급자 CDN 만료 시각이 포함되어 있으므로 즉시 재생해야 합니다.
    track·artist는 실제로 선택된 트랙 값이고, requested에는 클라이언트가 보낸
    원래 track·artist가 들어갑니다. provider와 provider_track_id로 어느
    카탈로그의 어느 곡인지 확정됩니다.
    """
    http: httpx.AsyncClient = request.app.state.http
    try:
        match = await _resolve_media(http, track, artist)
    except PreviewProviderUnavailable as exc:
        raise _provider_unavailable_http_error(exc) from exc

    if not match:
        raise HTTPException(
            status_code=404,
            detail=f"'{track}'의 미리 듣기를 찾을 수 없습니다.",
        )

    # track·artist는 요청값 echo가 아니라 실제로 고른 트랙이다.
    # 어떤 곡이 재생되는지 클라이언트가 확인할 수 있어야 한다.
    return {
        "preview_url": match.preview_url,
        "provider": match.provider,
        "provider_track_id": match.provider_track_id,
        "content_type": match.content_type,
        "artwork_url": match.artwork_url,
        "track": match.resolved_title,
        "artist": match.resolved_artist,
        "requested": {"track": track, "artist": artist},
    }


# ── GET /preview/stream ──────────────────────────────────────────


@router.get("/preview/stream")
async def stream_preview(
    request: Request,
    track: str = Query(..., min_length=1, max_length=200, description="곡명"),
    artist: str = Query(default="", max_length=200, description="아티스트명 (선택)"),
):
    """30초 오디오를 서버 경유로 스트리밍합니다.

    웹 브라우저 환경에서 공급자 CDN CORS 차단이 발생할 경우 사용하세요.
    CORS 제약이 없는 클라이언트라면 /preview 에서 받은 URL을 직접 재생하는 것이
    더 효율적입니다.
    """
    http: httpx.AsyncClient = request.app.state.http
    try:
        match = await _resolve_media(http, track, artist)
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
        media_type=match.content_type,
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": _content_disposition(
                match.resolved_title, match.file_extension
            ),
        },
    )
