import asyncio
import logging
import math
import re
import time
from typing import Any, Literal

import httpx
import pylast
from async_lru import alru_cache

from app.llm.llm_response import MusicQueryAnalysis
from app.llm.llm_wrapper import GeminiWrapper
from app.llm.prompt import MUSIC_QUERY_ANALYSIS_PROMPT
from app.services.catalog import CatalogClient, DeezerRateLimitError
from app.utils.text import compact_text
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)

ITUNES_URL = "https://itunes.apple.com/search"
DEEZER_URL = "https://api.deezer.com"
_API_SEMAPHORE = asyncio.Semaphore(25)

# Deezer circuit breaker — 429 감지 시 해당 시각까지 모든 Deezer 호출 스킵
_DZ_RATE_LIMIT_UNTIL: float = 0.0


def _is_dz_rate_limited() -> bool:
    return time.monotonic() < _DZ_RATE_LIMIT_UNTIL


def _mark_dz_rate_limited(headers: dict) -> None:
    global _DZ_RATE_LIMIT_UNTIL
    retry_after = int(headers.get("Retry-After", 60))
    _DZ_RATE_LIMIT_UNTIL = max(_DZ_RATE_LIMIT_UNTIL, time.monotonic() + retry_after)
    logger.warning("[Deezer] 429 — %d초 차단", retry_after)


_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float) -> tuple[bool, Any]:
    entry = _cache.get(key)
    if entry and time.monotonic() - entry[0] < ttl:
        return True, entry[1]
    return False, None


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)


async def _lf_call(key: str, ttl: float, fn, *args) -> Any:
    hit, cached = _cache_get(key, ttl)
    if hit:
        return cached
    result = await asyncio.to_thread(fn, *args)
    _cache_set(key, result)
    return result


_QUERY_ALIASES = ((("윤하", "사건의지평선"), "Event Horizon", "Younha"),)


async def preprocess_input(
    query: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
) -> tuple[str | None, str | None, str | None]:
    """
    유저에게 입력받은 자유로운 형태의 query(ex : 힙한 음악)를 track name, artist, itunes_uid 형식으로 return
    먼저 itunes(apple music)에서 검색하고 검색 결과가 있는 경우는 itunes_id도 함께 return한다.
    만약, itunes에서 검색결과가 없는 경우는 lastfm에서 search하고 track name과 artist를 return
    """

    # NOTE : 이거 필요한가? 그냥 하드코딩 같은데.
    alias = _known_query_alias(query)

    if alias:
        alias_name, alias_artist = alias
        item = await _itunes_search(
            http, alias_name, alias_artist, limit=8, min_score=0.65
        )
        if item:
            name = str(item.get("trackName") or "").strip()
            artist = str(item.get("artistName") or "").strip()
            itunes_source_id = _itunes_source_id(item)
            if name and artist:
                logger.info("[Normalize] known alias via iTunes: %s - %s", name, artist)
                return name, artist, itunes_source_id

        logger.info(
            "[Normalize] known alias fallback: %s - %s", alias_name, alias_artist
        )
        return alias_name, alias_artist, None

    # itunes search API로 노래의 이름, 아티스트, itunes에 등록된 id를 가져옴
    item = await _itunes_search(http, query, limit=8, min_score=0.35)
    if item:
        name = str(item.get("trackName") or "").strip()
        artist = str(item.get("artistName") or "").strip()
        itunes_source_id = _itunes_source_id(item)
        if name and artist:
            logger.info("[Normalize] iTunes: %s - %s", name, artist)
            return name, artist, itunes_source_id

    # itunes search API로 노래가 검색되지 않는 경우
    try:
        # lastfm에서 검색
        search = lastfm.search_for_track("", query)
        results = await _lf_call(
            f"lf:search_track:{compact_text(query)}",
            300,
            search.get_next_page,
        )

        # 검색결과가 없는 경우
        if not results:
            logger.warning("[Normalize] No search in Last.fm")
            return None, None, None

        for track in results:
            name = str(track.get_name() or "").strip()
            artist = str(track.get_artist().get_name() or "").strip()
            if name and artist and artist.lower() != "[unknown]":
                logger.info("[Normalize] Last.fm fallback: %s - %s", name, artist)
                return name, artist, None
    except Exception as exc:
        logger.warning("[Normalize] Last.fm fallback failed: %s", exc)

    return None, None, None


@alru_cache(maxsize=500, ttl=3600)
async def _itunes_search(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str = "",
    limit: int = 5,
    min_score: float = 0.5,
) -> dict[str, Any] | None:
    """
    itunes search API를 활용하여 음원 정보 검색
    """
    term = f"{track_name} {artist}".strip()
    if not term:
        return None
    return await CatalogClient(http).itunes_search_best(
        track_name, artist, limit, min_score
    )


@alru_cache(maxsize=500, ttl=3600)
async def _deezer_search(
    http: httpx.AsyncClient, track_name: str, artist: str
) -> dict[str, Any] | None:
    # rate-limit은 None 대신 예외로 전파한다.
    # alru_cache는 예외가 난 호출을 캐시하지 않으므로(429가 1시간 None으로 고착되는 것을 방지),
    # 회로차단기가 풀리면 다음 호출에서 곧바로 재시도된다.
    if _is_dz_rate_limited():
        raise DeezerRateLimitError()
    try:
        # TODO : Catalog 해체분석 해보기.
        return await CatalogClient(http).deezer_search_best(track_name, artist)
    except DeezerRateLimitError as e:
        _mark_dz_rate_limited({"Retry-After": str(e.retry_after)})
        raise


async def _deezer_or_none(
    http: httpx.AsyncClient, track_name: str, artist: str
) -> dict[str, Any] | None:
    """rate-limit 예외를 호출부에서는 None으로 흡수한다."""
    try:
        return await _deezer_search(http, track_name, artist)
    except DeezerRateLimitError:
        return None


async def get_tracks_metadata(
    http: httpx.AsyncClient,
    tracks: list[TrackInfo],
    lastfm: pylast.LastFMNetwork | None = None,
    fields: list[Literal["popularity", "album_art", "source_id"]]
    | Literal["all"] = "all",
) -> list[TrackInfo]:
    """
    deezer/itunes에서 track의 metadata를 검색해 TrackInfo에 추가합니다.
    - popularity : track의 인기도. 곡 재생횟수를 log-scale로 normalize한 value
    - album_art : 앨범 커버(표지) url
    - source_id : itunes/deezer에 등록된 track의 id
    """
    VALID_FIELDS = {"popularity", "album_art", "source_id"}
    if fields == "all":
        active_fields = VALID_FIELDS
    else:
        validate_fields = set(fields) - VALID_FIELDS
        if validate_fields:
            raise ValueError(f"유효하지 않은 메타데이터 필드입니다: {validate_fields}")
        active_fields = set(fields)

    async def _fetch(track: TrackInfo) -> TrackInfo:
        req_art = "album_art" in active_fields
        req_id = "source_id" in active_fields

        needs_deezer = "popularity" in active_fields
        needs_itunes = req_art or req_id

        # field에 따라 필요한 API만 call
        tasks = []
        if needs_deezer:
            tasks.append(_deezer_or_none(http, track.name, track.artist))
        if needs_itunes:
            tasks.append(
                _itunes_search(http, track.name, track.artist, limit=8, min_score=0.45)
            )

        async with _API_SEMAPHORE:
            results = await asyncio.gather(*tasks)

        result_iter = iter(results)
        deezer_item = next(result_iter) if needs_deezer else None
        itunes_item = next(result_iter) if needs_itunes else None

        # popularity
        if "popularity" in active_fields:
            if deezer_item:
                popularity = int(deezer_item.get("rank") or 0)
            else:
                popularity = 0
            if popularity > 0:
                # rank 정규화
                track.popularity = min(100, int(math.log10(popularity + 1) * 10))

        # album_art_url & album srouce id
        if req_art or req_id:
            # 1. req에 따라 album art/id를 itunes에서 가져옴
            if itunes_item:
                if req_art:
                    track.album_art_url = (
                        _itunes_artwork(itunes_item) or track.album_art_url
                    )
                if req_id:
                    track.source_id = _itunes_source_id(itunes_item) or track.source_id

            # 2. itune와 req field 확인 후 API return 값 확인
            is_missing_art = req_art and not track.album_art_url
            is_missing_id = req_id and not track.source_id

            # 3. itunes에서 원하는 metadata를 가져오지 못한 경우는 deezer API를 call
            if is_missing_art or is_missing_id:
                if not deezer_item:
                    async with _API_SEMAPHORE:
                        deezer_item = await _deezer_or_none(
                            http, track.name, track.artist
                        )

                if deezer_item:
                    # 4-1. deezer에서 album art
                    if is_missing_art:
                        album = deezer_item.get("album") or {}
                        track.album_art_url = (
                            album.get("cover_big")
                            or album.get("cover_medium")
                            or album.get("cover")
                        )
                        # TODO : id 없는경우 warning logger 추가

                    # 4-2. deezer에서 source id
                    if is_missing_id:
                        track.source_id = _deezer_source_id(deezer_item)

            # 5. deezer에도 album art가 없는 경우 lastfm에서 가져옴
            is_missing_art = req_art and not track.album_art_url
            if is_missing_art and lastfm:
                try:
                    lf_track_obj = lastfm.get_track(track.artist, track.name)
                    artwork = await _lf_call(
                        f"lf:cover:{compact_text(track.artist)}:{compact_text(track.name)}",
                        600,
                        lf_track_obj.get_cover_image,
                    )
                    if artwork:
                        track.album_art_url = str(artwork)
                except Exception:
                    # TODO : art 없는경우 warning logger 추가
                    pass
        return track

    return list(await asyncio.gather(*[_fetch(t) for t in tracks]))


def _known_query_alias(query: str) -> tuple[str, str] | None:
    compact_query = compact_text(query)
    for tokens, title, artist in _QUERY_ALIASES:
        if all(compact_text(token) in compact_query for token in tokens):
            return title, artist
    return None


def _itunes_artwork(item: dict[str, Any]) -> str | None:
    url = str(item.get("artworkUrl100") or "").strip()
    if not url:
        return None
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/600x600bb.\1", url)


def _itunes_source_id(item: dict[str, Any]) -> str | None:
    track_id = item.get("trackId")
    return f"itunes:{track_id}" if track_id else None


def _deezer_source_id(item: dict[str, Any]) -> str | None:
    track_id = item.get("id")
    return f"deezer:{track_id}" if track_id else None


def analyze_music_query(
    query: str,
    gemini_wrapper: GeminiWrapper,
) -> MusicQueryAnalysis:
    """
    유저의 자유로운 형태의 query를 분석하여 direct, mood, meaningless 중 하나로 분류합니다.
    * direct인 경우에는 검색어, 곡 제목, 아티스트 이름, 대체 검색어를 반환
    * mood인 경우에는 추천 검색에 사용할 음악 태그와 제외할 태그를 반환
    """
    raw_response = gemini_wrapper.request(
        system_prompt=MUSIC_QUERY_ANALYSIS_PROMPT,
        user_prompt=query,
        temperature=0.1,
        max_output_tokens=500,
        response_schema=MusicQueryAnalysis,
        response_validator=MusicQueryAnalysis,
    )
    return raw_response
