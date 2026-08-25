import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Literal

import httpx
import pylast
from async_lru import alru_cache

from app.llm.llm_response import AlternativeQuery, MusicQueryAnalysis
from app.llm.llm_wrapper import GeminiWrapper
from app.llm.prompt import MUSIC_QUERY_ANALYSIS_PROMPT
from app.services.catalog import (
    CatalogClient,
    DeezerRateLimitError,
    ItunesRateLimitError,
    _alias_artist_score,
    _alias_match_score,
    _looks_like_bad_version,
)
from app.utils.text import compact_text
from recommend_algo.common.models import ProviderBinding, TrackInfo

logger = logging.getLogger(__name__)

ITUNES_URL = "https://itunes.apple.com/search"
DEEZER_URL = "https://api.deezer.com"
_API_SEMAPHORE = asyncio.Semaphore(25)
_ITUNES_SEMAPHORE = asyncio.Semaphore(8)

# Last.fm은 동기 라이브러리(pylast)를 to_thread로 호출한다. 기본 스레드풀이 32개뿐이라
# hidden 버킷의 팬아웃(유사 아티스트 최대 30명)만으로도 풀을 점유할 수 있어 상한을 둔다.
_LASTFM_SEMAPHORE = asyncio.Semaphore(8)
# pylast 호출에는 자체 데드라인이 없다. 행이 걸리면 요청이 무한 대기하므로 상한을 건다.
# ponytail: to_thread는 취소가 안 되므로 타임아웃 시 스레드는 남아서 끝까지 돈다.
# 스레드 누수가 실제로 문제되면 pylast를 async HTTP 호출로 교체해야 한다.
_LASTFM_CALL_TIMEOUT = 8.0

# Last.fm은 구체적인 수치를 공개하지 않는다. ToS §4.4는 "재량으로 제한한다"일
# 뿐이고, 실행 가능한 규칙은 API 소개 문서의 한 문장뿐이다 — "계속해서 초당
# 여러 번 호출하면 계정이 정지될 수 있다". 널리 인용되는 "초당 5회, 5분 평균"은
# 현재 공식 문서에 없다.
#
# 그 문장이 금지하는 것은 봉우리가 아니라 '지속'이다. 그래서 토큰 버킷을 쓴다.
# 한 요청의 팬아웃(실측 41회)은 모아 둔 토큰으로 한 번에 통과하고, 그 뒤로는
# 채워지는 속도 이상으로 나가지 못한다. 세마포어는 동시 실행 수만 제어하므로
# 이것을 대신하지 못한다.
#
# ponytail: 아래 두 값은 근거 있는 상수가 아니라 정책값이다. 공개된 수치가
# 없으므로 code 29가 로그에 보이면 refill부터 낮춘다. 프로세스 로컬이라
# 인스턴스가 늘면 그만큼 곱해진다 — 캐시·회로차단기와 함께 옮겨야 한다.
_LASTFM_BURST = 45.0
_LASTFM_REFILL_PER_SECOND = 5.0
_LASTFM_TOKENS: float = _LASTFM_BURST
_LASTFM_TOKENS_UPDATED: float = time.monotonic()
_LASTFM_RATE_LIMIT_COOLDOWN = 60.0
_LASTFM_RATE_LIMIT_UNTIL: float = 0.0

# Deezer circuit breaker — 429 감지 시 해당 시각까지 모든 Deezer 호출 스킵
_DZ_RATE_LIMIT_UNTIL: float = 0.0
_ITUNES_RATE_LIMIT_UNTIL: float = 0.0

# 후보를 곡으로 확정하는 최소 _catalog_match_score(title 0.68 / artist 0.32 가중).
_ITUNES_CONFIRM_SCORE = 0.62
_LASTFM_CONFIRM_SCORE = 0.5
# 총점과 별개로 요구하는 아티스트 일치 하한. 제목만 정확한 오답은 총점이 0.68까지
# 나오므로 총점 문턱만으로는 막을 수 없다.
# ponytail: fixture로 검증한 정책값이지 증명된 상수가 아니다. 이 하한은 원어 표기
# 시도를 통째로 탈락시키므로("밤편지/아이유" 대 iTunes의 "IU") 영문 대체 표기가
# 반드시 함께 와야 한다. alias가 늘면 test_recommend_algo.py 회귀 케이스로 재보정한다.
_ARTIST_MIN_SCORE = 0.5
# 제목·아티스트가 모두 완전 일치. 더 나은 후보가 있을 수 없으므로 즉시 종료한다.
_PERFECT_MATCH_SCORE = 1.0


def _is_dz_rate_limited() -> bool:
    return time.monotonic() < _DZ_RATE_LIMIT_UNTIL


def _mark_dz_rate_limited(headers: dict) -> None:
    global _DZ_RATE_LIMIT_UNTIL
    retry_after = int(headers.get("Retry-After", 60))
    _DZ_RATE_LIMIT_UNTIL = max(_DZ_RATE_LIMIT_UNTIL, time.monotonic() + retry_after)
    logger.warning("[Deezer] 429 — %d초 차단", retry_after)


class LastfmRateLimitError(Exception):
    """Last.fm이 호출 제한을 알렸거나 차단 중이라 호출하지 않았다.

    호출부는 이 예외를 따로 잡지 않는다. 버킷 단위 예외 처리가 빈 결과로
    떨어뜨리는데, 제한 중에는 그게 맞는 동작이다 — 계속 두드리면 제한이 길어진다.
    """


def _is_lf_rate_limited() -> bool:
    return time.monotonic() < _LASTFM_RATE_LIMIT_UNTIL


def _mark_lf_rate_limited(cooldown: float = _LASTFM_RATE_LIMIT_COOLDOWN) -> None:
    global _LASTFM_RATE_LIMIT_UNTIL
    _LASTFM_RATE_LIMIT_UNTIL = max(
        _LASTFM_RATE_LIMIT_UNTIL,
        time.monotonic() + cooldown,
    )
    logger.warning("[Last.fm] 호출 제한 — %d초 차단", cooldown)


def _is_lastfm_rate_limit_error(exc: BaseException) -> bool:
    """pylast는 제한을 XML 본문의 error code 29로 전달한다.

    HTTP 429를 따로 보지 않는다. pylast가 5xx만 상태 코드로 구분하고 나머지는
    본문을 파싱하기 때문이다. `status`는 본문에서 온 문자열이라 숫자와 비교하기
    전에 문자열로 맞춘다.
    """
    return (
        isinstance(exc, pylast.WSError)
        and str(getattr(exc, "status", "")) == str(pylast.STATUS_RATE_LIMIT_EXCEEDED)
    )


def _release_lastfm_slot(task: asyncio.Task) -> None:
    """호출이 끝나면 차단 여부를 먼저 기록하고 자리를 돌려준다.

    순서가 중요하다. 자리를 먼저 놓으면 기다리던 호출이 깨어나 차단기를 확인하는
    시점이 기록보다 빨라, 방금 거절당한 것을 모른 채 그대로 나간다. 호출부의
    `except`에서 기록하면 이 경합이 그대로 생긴다 — 자리는 태스크가 끝나는 순간
    풀리는데 `except`는 그 뒤에 깨어나기 때문이다.
    """
    if not task.cancelled():
        exc = task.exception()
        if exc is not None and _is_lastfm_rate_limit_error(exc):
            _mark_lf_rate_limited()
    _LASTFM_SEMAPHORE.release()


async def _await_lastfm_permission() -> None:
    """호출해도 되는 상태가 될 때까지 기다린다. 차단기와 토큰을 함께 본다.

    호출 직전에 부른다. 토큰을 미리 잡아 두면 세마포어를 기다리는 동안 그 표가
    낡아, 실제로는 몰려 나가는 호출이 장부에는 흩어진 것으로 남는다.

    차단기를 매 바퀴 확인하는 것이 핵심이다. 토큰이 마른 상태가 곧 지속 트래픽이고
    그때가 제한을 받기 가장 쉬운 순간인데, 한 번만 확인하면 기다리는 동안 다른
    호출이 거절당해 차단기가 열려도 토큰이 채워지는 순간 그대로 나간다.

    잠든 호출들이 한꺼번에 깨어나도 다시 확인하므로 토큰 하나를 여럿이 나눠
    갖지 않는다.
    """
    global _LASTFM_TOKENS, _LASTFM_TOKENS_UPDATED
    while True:
        if _is_lf_rate_limited():
            raise LastfmRateLimitError("Last.fm 호출 제한 중")
        now = time.monotonic()
        _LASTFM_TOKENS = min(
            _LASTFM_BURST,
            _LASTFM_TOKENS + (now - _LASTFM_TOKENS_UPDATED) * _LASTFM_REFILL_PER_SECOND,
        )
        _LASTFM_TOKENS_UPDATED = now
        # 부동소수 오차로 0.9999...가 나오면 제자리를 돈다. 기다린 만큼은
        # 채워진 것으로 인정한다.
        if _LASTFM_TOKENS + 1e-9 >= 1.0:
            _LASTFM_TOKENS = max(0.0, _LASTFM_TOKENS - 1.0)
            return
        wait = (1.0 - _LASTFM_TOKENS) / _LASTFM_REFILL_PER_SECOND
        logger.info("[Last.fm] 지속 호출 상한 — %.2f초 대기", wait)
        await asyncio.sleep(wait)


def _is_itunes_rate_limited() -> bool:
    return time.monotonic() < _ITUNES_RATE_LIMIT_UNTIL


def _mark_itunes_rate_limited(retry_after: int) -> None:
    global _ITUNES_RATE_LIMIT_UNTIL
    _ITUNES_RATE_LIMIT_UNTIL = max(
        _ITUNES_RATE_LIMIT_UNTIL,
        time.monotonic() + retry_after,
    )
    logger.warning("[iTunes] 429 — %d초 차단", retry_after)


# 만료 항목은 조회 시 버리고, 그래도 남는 증가분은 LRU로 잘라낸다.
# 상한이 없으면 lf:cover:{artist}:{track} 키가 본 곡 수만큼 계속 쌓인다.
# ponytail: 프로세스 로컬 캐시. 인스턴스가 여러 개로 늘면 공용 캐시로 옮겨야 적중률이 산다.
_CACHE_MAX_ENTRIES = 2000
_cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()


def _cache_get(key: str, ttl: float) -> tuple[bool, Any]:
    entry = _cache.get(key)
    if entry is None:
        return False, None
    if time.monotonic() - entry[0] >= ttl:
        del _cache[key]
        return False, None
    _cache.move_to_end(key)
    return True, entry[1]


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


async def _lf_call(key: str, ttl: float, fn, *args) -> Any:
    hit, cached = _cache_get(key, ttl)
    if hit:
        return cached
    # 허가 확인은 세마포어를 잡은 뒤에, 호출 직전에 한다. 진입 전에 한 번만 보면
    # 이미 줄을 선 호출들은 그사이에 차단기가 열려도 그대로 나간다 — 팬아웃이
    # 41회인 경로에서는 첫 거절 뒤에도 나머지가 계속 두드린다.
    await _LASTFM_SEMAPHORE.acquire()
    try:
        await _await_lastfm_permission()
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
    except BaseException:
        _LASTFM_SEMAPHORE.release()
        raise
    task.add_done_callback(_release_lastfm_slot)
    try:
        result = await asyncio.wait_for(
            asyncio.shield(task), timeout=_LASTFM_CALL_TIMEOUT
        )
    except Exception as exc:
        if _is_lastfm_rate_limit_error(exc):
            raise LastfmRateLimitError("Last.fm 호출 제한") from exc
        raise
    _cache_set(key, result)
    return result


ResolvedTrack = tuple[str, str, str | None]


async def preprocess_input(
    query: str,
    alternative_queries: list[AlternativeQuery],
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    track_title: str | None = None,
    artist_name: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    유저에게 입력받은 자유로운 형태의 query(ex : 힙한 음악)를 track name, artist, itunes_uid 형식으로 return
    먼저 itunes(apple music)에서 검색하고 검색 결과가 있는 경우는 itunes_id도 함께 return한다.
    만약, itunes에서 검색결과가 없는 경우는 lastfm에서 search하고 track name과 artist를 return
    """

    lookups = _structured_lookups(track_title, artist_name, alternative_queries)

    if lookups:
        resolved = await _itunes_structured(lookups, http)
    else:
        # 제목이나 아티스트를 특정하지 못한 요청(artist-only 등)만 문자열 검색을 쓴다.
        resolved = await _itunes_loose(query, alternative_queries, http)
    if resolved:
        return resolved

    return await _lastfm_search(query, lookups, lastfm) or (None, None, None)


def _structured_lookups(
    track_title: str | None,
    artist_name: str | None,
    alternative_queries: list[AlternativeQuery],
) -> list[tuple[str, str]]:
    """제목과 아티스트가 모두 있는 (원표기, 대체표기) 쌍만 중복 없이 모은다."""
    pairs = [(track_title or "", artist_name or "")]
    pairs += [(alt.track_title, alt.artist_name) for alt in alternative_queries]

    seen: set[tuple[str, str]] = set()
    lookups: list[tuple[str, str]] = []
    for title, artist in pairs:
        title, artist = title.strip(), artist.strip()
        key = (compact_text(title), compact_text(artist))
        if not title or not artist or key in seen:
            continue
        seen.add(key)
        lookups.append((title, artist))
    return lookups


async def _itunes_structured(
    lookups: list[tuple[str, str]], http: httpx.AsyncClient
) -> ResolvedTrack | None:
    """아티스트 하한을 통과한 후보만 모아 비교한다.

    표기별로 즉시 반환하지 않는 이유는, 원표기에서 나온 약한 후보가 대체 표기의
    정확한 후보를 가로막기 때문이다. 완전 일치는 더 나은 후보가 있을 수 없으므로
    그 자리에서 끊어 iTunes 호출을 아낀다.
    """
    title_aliases = tuple(title for title, _ in lookups)
    artist_aliases = tuple(artist for _, artist in lookups)

    best: tuple[float, ResolvedTrack] | None = None
    for title, artist in lookups:
        item = await _itunes_or_none(
            http,
            title,
            artist,
            limit=8,
            min_score=_ITUNES_CONFIRM_SCORE,
            min_artist_score=_ARTIST_MIN_SCORE,
            title_aliases=title_aliases,
            artist_aliases=artist_aliases,
        )
        track = _itunes_track(item)
        if not track:
            continue
        score = _alias_match_score(track[0], track[1], title_aliases, artist_aliases)
        if score >= _PERFECT_MATCH_SCORE:
            logger.info("[Normalize] iTunes exact: %s - %s", track[0], track[1])
            return track
        if best is None or score > best[0]:
            best = (score, track)

    if best:
        logger.info("[Normalize] iTunes: %s - %s", best[1][0], best[1][1])
        return best[1]
    return None


async def _itunes_loose(
    query: str,
    alternative_queries: list[AlternativeQuery],
    http: httpx.AsyncClient,
) -> ResolvedTrack | None:
    terms = [query] + [
        f"{alt.track_title} {alt.artist_name}".strip() for alt in alternative_queries
    ]
    for term in terms:
        if not term.strip():
            continue
        item = await _itunes_or_none(http, term, limit=8, min_score=0.35)
        track = _itunes_track(item)
        if track:
            logger.info("[Normalize] iTunes(loose): %s - %s", track[0], track[1])
            return track
    return None


async def _lastfm_search(
    query: str,
    lookups: list[tuple[str, str]],
    lastfm: pylast.LastFMNetwork,
) -> ResolvedTrack | None:
    terms = [f"{title} {artist}" for title, artist in lookups] or [query]
    try:
        for term in terms:
            search = lastfm.search_for_track("", term)
            results = await _lf_call(
                f"lf:search_track:{compact_text(term)}",
                300,
                search.get_next_page,
            )
            if not results:
                logger.warning("[Normalize] No search in Last.fm for query: %s", term)
                continue

            for track in results:
                name = str(track.get_name() or "").strip()
                artist = str(track.get_artist().get_name() or "").strip()
                if not name or not artist or artist.lower() == "[unknown]":
                    continue
                if _looks_like_bad_version(name) or _looks_like_bad_version(
                    artist, title_context=False
                ):
                    continue
                # Last.fm 검색은 점수를 주지 않는다. 아는 표기가 있으면 iTunes와 같은
                # 기준으로 거르고, 없으면 기존대로 첫 유효 결과를 쓴다.
                if lookups and not _matches_lookups(name, artist, lookups):
                    continue
                logger.info("[Normalize] Last.fm: %s - %s", name, artist)
                return name, artist, None
    except Exception as exc:
        logger.warning("[Normalize] Last.fm search failed: %s", exc)
    return None


def _matches_lookups(name: str, artist: str, lookups: list[tuple[str, str]]) -> bool:
    """iTunes와 같은 기준으로 거른다. 제목·아티스트를 표기별로 독립 채점한다."""
    titles = tuple(title for title, _ in lookups)
    artists = tuple(artist_alias for _, artist_alias in lookups)
    return (
        _alias_artist_score(artist, artists) >= _ARTIST_MIN_SCORE
        and _alias_match_score(name, artist, titles, artists) >= _LASTFM_CONFIRM_SCORE
    )


def _itunes_track(item: dict[str, Any] | None) -> ResolvedTrack | None:
    if not item:
        return None
    name = str(item.get("trackName") or "").strip()
    artist = str(item.get("artistName") or "").strip()
    if not name or not artist:
        return None
    binding = _itunes_binding(item)
    return name, artist, binding.source_id if binding else None


@alru_cache(maxsize=500, ttl=3600)
async def _itunes_search(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str = "",
    limit: int = 5,
    min_score: float = 0.5,
    min_artist_score: float = 0.0,
    title_aliases: tuple[str, ...] = (),
    artist_aliases: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """
    itunes search API를 활용하여 음원 정보 검색
    """
    term = f"{track_name} {artist}".strip()
    if not term:
        return None
    async with _ITUNES_SEMAPHORE:
        if _is_itunes_rate_limited():
            raise ItunesRateLimitError()
        try:
            return await CatalogClient(http).itunes_search_best(
                track_name,
                artist,
                limit,
                min_score,
                min_artist_score,
                title_aliases,
                artist_aliases,
            )
        except ItunesRateLimitError as exc:
            _mark_itunes_rate_limited(exc.retry_after)
            raise


async def _itunes_or_none(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str = "",
    limit: int = 5,
    min_score: float = 0.5,
    min_artist_score: float = 0.0,
    title_aliases: tuple[str, ...] = (),
    artist_aliases: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    try:
        return await _itunes_search(
            http,
            track_name,
            artist,
            limit,
            min_score,
            min_artist_score,
            title_aliases,
            artist_aliases,
        )
    except ItunesRateLimitError:
        return None


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
    fields: list[Literal["album_art", "source_id"]] | Literal["all"] = "all",
) -> list[TrackInfo]:
    """
    itunes/deezer에서 track의 metadata를 검색해 TrackInfo에 추가합니다.
    - album_art : 앨범 커버(표지) url
    - source_id : itunes/deezer에 등록된 track의 id

    `popularity`는 더 이상 여기서 받지 않는다. 후보마다 Deezer를 부르던 fan-out
    이었고, 노출도는 이제 Last.fm 응답이 이미 준 값으로 계산한다
    (`scoring.assign_exposure`). Deezer는 iTunes가 앨범아트나 ID를 주지 못한
    경우의 fallback으로만 남는다.
    """
    VALID_FIELDS = {"album_art", "source_id"}
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

        needs_itunes = req_art or req_id

        # field에 따라 필요한 API만 call
        tasks = []
        if needs_itunes:
            tasks.append(
                _itunes_or_none(
                    http,
                    track.name,
                    track.artist,
                    limit=8,
                    min_score=0.45,
                    # 총점 문턱만으로는 제목이 정확한 오답을 막을 수 없다.
                    # 가중치가 title 0.68 / artist 0.32라 아티스트가 전혀 달라도
                    # 0.68이 나온다. 이 경로에만 하한이 빠져 있어서, 기준곡이
                    # 동명이곡의 앨범아트와 source_id로 확정될 수 있었다.
                    # source_id는 preview가 그대로 재생하므로 다른 곡이 나온다.
                    # Deezer 폴백은 `_select_deezer_item`이 이미 걸러낸다.
                    min_artist_score=_ARTIST_MIN_SCORE,
                )
            )

        async with _API_SEMAPHORE:
            results = await asyncio.gather(*tasks)

        result_iter = iter(results)
        deezer_item = None
        itunes_item = next(result_iter) if needs_itunes else None

        # album_art_url & album srouce id
        if req_art or req_id:
            # 1. req에 따라 album art/id를 itunes에서 가져옴
            if itunes_item:
                if req_art:
                    track.album_art_url = (
                        _itunes_artwork(itunes_item) or track.album_art_url
                    )
                if req_id:
                    track.bind(_itunes_binding(itunes_item))

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
                        track.bind(_deezer_binding(deezer_item))

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


def _itunes_artwork(item: dict[str, Any]) -> str | None:
    url = str(item.get("artworkUrl100") or "").strip()
    if not url:
        return None
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/600x600bb.\1", url)


def _itunes_binding(item: dict[str, Any]) -> ProviderBinding | None:
    track_id = item.get("trackId")
    if not track_id:
        return None
    return ProviderBinding(
        provider="itunes",
        provider_track_id=str(track_id),
        resolved_title=str(item.get("trackName") or "").strip(),
        resolved_artist=str(item.get("artistName") or "").strip(),
    )


def _deezer_binding(item: dict[str, Any]) -> ProviderBinding | None:
    track_id = item.get("id")
    if not track_id:
        return None
    # Deezer의 artist는 dict가 정상이지만 문자열이 오는 응답도 있다.
    artist = item.get("artist")
    resolved_artist = artist.get("name") if isinstance(artist, dict) else artist
    return ProviderBinding(
        provider="deezer",
        provider_track_id=str(track_id),
        resolved_title=str(item.get("title") or "").strip(),
        resolved_artist=str(resolved_artist or "").strip(),
    )


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
