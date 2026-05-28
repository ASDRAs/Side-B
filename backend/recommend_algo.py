"""
recommend_algo.py (iTunes + Deezer + Last.fm)

Normalization: iTunes first, Last.fm fallback
Metadata: iTunes artwork first, Deezer rank/artwork as secondary data
Recommendations: Last.fm similarity/tag APIs
"""

import asyncio
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import pylast

from app.config.rules import MOOD_QUERY_RULES as _MOOD_QUERY_RULES
from app.services.catalog import CatalogClient, DeezerRateLimitError
from app.utils.text import compact_text, text_ratio

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

_TRACK_SIMILAR_ALIASES = {
    (compact_text("Younha"), compact_text("혜성")): [("ほうき星", "Younha")],
}

_KNOWN_KOREAN_ARTISTS = {
    "younha",
    "iu",
    "bts",
    "blackpink",
    "newjeans",
    "qwer",
    "day6",
    "akmu",
    "taeyeon",
    "aespa",
    "twice",
    "ive",
    "le sserafim",
}

_OPPOSITE_TAGS = {
    "ballad": ["upbeat", "dance", "pop"],
    "sad": ["happy", "upbeat", "dance"],
    "melancholy": ["happy", "upbeat", "pop"],
    "acoustic": ["electronic", "synthpop", "dance"],
    "rock": ["acoustic", "chill", "pop"],
    "indie": ["pop", "dance", "electronic"],
    "pop": ["indie", "alternative", "electronic"],
    "k-pop": ["indie pop", "synthpop", "pop"],
    "female vocalists": ["indie", "alternative", "pop"],
}

_TAG_QUERY_RULES = (
    (
        ("시티팝", "citypop", "city pop"),
        ["korean city pop", "citypop", "japanese city pop", "city pop"],
    ),
    (("케이팝", "kpop", "k-pop", "아이돌"), ["k-pop", "dance-pop", "korean"]),
    (("발라드", "ballad"), ["k-ballad", "ballad", "korean"]),
    (("인디", "indie"), ["indie", "indie pop", "k-indie"]),
    (("알앤비", "rnb", "r&b"), ["rnb", "soul", "k-rnb"]),
    (("재즈", "jazz"), ["jazz", "vocal jazz"]),
    (("락", "록", "rock"), ["rock", "alternative"]),
    (("로파이", "lofi", "lo-fi"), ["lo-fi", "chill", "instrumental"]),
)


@dataclass
class TrackInfo:
    name: str
    artist: str
    source_id: str | None = None
    album_art_url: str | None = None
    popularity: int | None = None
    match_score: float | None = None
    reverse_score: float | None = None
    algo: str = ""
    label: str = ""
    reason_tags: list[str] = field(default_factory=list)


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


async def resolve_album_art(
    http: httpx.AsyncClient,
    track_name: str,
    artist: str,
) -> tuple[str | None, str | None]:
    """Return (source_id, album_art_url) from the configured public catalogs."""
    item = await _itunes_search(http, track_name, artist, limit=8, min_score=0.45)
    if item:
        artwork = _itunes_artwork(item)
        if artwork:
            return _itunes_source_id(item), artwork

    item = await _dz_search(http, track_name, artist)
    if item:
        album = item.get("album") or {}
        artwork = (
            album.get("cover_big") or album.get("cover_medium") or album.get("cover")
        )
        if artwork:
            return _deezer_source_id(item), str(artwork)

    return None, None


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
    cache_key = f"itunes:{term}:{limit}:{min_score}"
    hit, cached = _cache_get(cache_key, 300)
    if hit:
        return cached
    result = await CatalogClient(http).itunes_search_best(
        track_name, artist, limit, min_score
    )
    _cache_set(cache_key, result)
    return result


async def _dz_search(
    http: httpx.AsyncClient, track_name: str, artist: str
) -> dict[str, Any] | None:
    if _is_dz_rate_limited():
        return None
    clean_name = re.sub(r"\(.*?\)|\[.*?\]", " ", track_name)
    clean_name = re.sub(
        r"\s+-\s+(remaster(?:ed)?|live|radio edit|single version).*$",
        " ",
        clean_name,
        flags=re.I,
    )
    clean_name = re.sub(r"\s+", " ", clean_name).strip()
    cache_key = f"deezer:{compact_text(clean_name)}:{compact_text(artist)}"
    hit, cached = _cache_get(cache_key, 300)
    if hit:
        return cached
    try:
        result = await CatalogClient(http).deezer_search_best(track_name, artist)
    except DeezerRateLimitError as e:
        _mark_dz_rate_limited({"Retry-After": str(e.retry_after)})
        return None
    _cache_set(cache_key, result)
    return result


async def _enrich_metadata(
    http: httpx.AsyncClient, tracks: list[TrackInfo]
) -> list[TrackInfo]:
    async def _fetch(track: TrackInfo) -> TrackInfo:
        deezer_item, itunes_item = await asyncio.gather(
            _dz_search(http, track.name, track.artist),
            _itunes_search(http, track.name, track.artist, limit=8, min_score=0.45),
        )

        if deezer_item:
            rank = int(deezer_item.get("rank") or 0)
            if rank > 0:
                track.popularity = min(100, int(math.log10(rank + 1) * 10))
            track.source_id = track.source_id or _deezer_source_id(deezer_item)

        if itunes_item:
            track.source_id = _itunes_source_id(itunes_item) or track.source_id
            track.album_art_url = _itunes_artwork(itunes_item) or track.album_art_url

        if not track.album_art_url and deezer_item:
            album = deezer_item.get("album") or {}
            track.album_art_url = (
                album.get("cover_big")
                or album.get("cover_medium")
                or album.get("cover")
            )

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


def _cap_per_artist(tracks: list[TrackInfo], max_per: int = 1) -> list[TrackInfo]:
    seen: dict[str, int] = {}
    result: list[TrackInfo] = []
    for track in tracks:
        key = track.artist.lower()
        if seen.get(key, 0) < max_per:
            seen[key] = seen.get(key, 0) + 1
            result.append(track)
    return result


def _diverse_top_n(
    pool: list[TrackInfo],
    top_n: int,
    *,
    score_fn=lambda t: t.reverse_score or 0,
    diversity: float = 0.3,
    candidate_mult: int = 3,
) -> list[TrackInfo]:
    """점수 정규화 후 Gaussian noise로 샘플링 — 같은 입력에도 매번 다른 결과."""
    if not pool:
        return []
    sorted_pool = sorted(pool, key=score_fn, reverse=True)
    if diversity <= 0.0:
        return sorted_pool[:top_n]
    n_candidates = min(len(pool), top_n * candidate_mult)
    candidates = sorted_pool[:n_candidates]
    raw_scores = [score_fn(t) for t in candidates]
    min_s, max_s = min(raw_scores), max(raw_scores)
    score_range = (max_s - min_s) or 1.0
    scored = [
        (t, (s - min_s) / score_range + random.gauss(0, diversity * 0.3))
        for t, s in zip(candidates, raw_scores)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:top_n]]


def _balanced_candidate_slice(
    tracks: list[TrackInfo],
    limit: int,
    *,
    score_fn=lambda t: t.match_score or 0,
) -> list[TrackInfo]:
    sorted_pool = sorted(tracks, key=score_fn, reverse=True)
    if len(sorted_pool) <= limit:
        return sorted_pool

    artist_count = len(
        {compact_text(track.artist) for track in sorted_pool if track.artist}
    )
    per_artist = max(2, math.ceil(limit / max(artist_count, 1)))
    balanced = _cap_per_artist(sorted_pool, max_per=per_artist)
    selected_keys = {_track_key(track) for track in balanced}
    if len(balanced) < limit:
        balanced.extend(
            track for track in sorted_pool if _track_key(track) not in selected_keys
        )
    return balanced[:limit]


def _fill_from_ranked_pool(
    primary: list[TrackInfo],
    fallback_pool: list[TrackInfo],
    top_n: int,
) -> list[TrackInfo]:
    selected = list(primary[:top_n])
    if len(selected) >= top_n:
        return selected
    selected_keys = {_track_key(track) for track in selected}
    for track in fallback_pool:
        key = _track_key(track)
        if key in selected_keys:
            continue
        selected.append(track)
        selected_keys.add(key)
        if len(selected) >= top_n:
            break
    return selected


def _dedupe_tracks(tracks: list[TrackInfo]) -> list[TrackInfo]:
    seen: set[str] = set()
    deduped: list[TrackInfo] = []
    for track in tracks:
        key = _track_key(track)
        if not key.strip(":") or key in seen:
            continue
        seen.add(key)
        deduped.append(track)
    return deduped


def _track_key(track: TrackInfo) -> str:
    return f"{compact_text(track.artist)}::{compact_text(track.name)}"


# def _track_similar_lookup_pairs(track_name: str, artist: str) -> list[tuple[str, str]]:
#     pairs = [(track_name, artist)]
#     aliases = _TRACK_SIMILAR_ALIASES.get(
#         (compact_text(artist), compact_text(track_name)), []
#     )
#     seen = {(compact_text(artist), compact_text(track_name))}
#     for alias_name, alias_artist in aliases:
#         key = (compact_text(alias_artist), compact_text(alias_name))
#         if key not in seen:
#             seen.add(key)
#             pairs.append((alias_name, alias_artist))
#     return pairs


async def _track_similar_tracks(
    track_name: str,
    artist: str,
    lastfm: pylast.LastFMNetwork,
    limit: int,
) -> None | list[pylast.SimilarItem]:
    last_error: Exception | None = None

    # 검색어 증강(last.fm에서 검색이 잘 되도록 _TRACK_SIMILAR_ALIASES에 미리 등록되어 있는 경우 추가함)
    # TODO : _TRACK_SIMILAR_ALIASES는 DB로 관리해도 괜찮을까?
    lookup_candidates = [(track_name, artist)]
    aliases = _TRACK_SIMILAR_ALIASES.get(
        (compact_text(artist), compact_text(track_name)), []
    )

    # 중복 제거
    already_seen = {(compact_text(artist), compact_text(track_name))}
    for alias_name, alias_artist in aliases:
        key = (compact_text(alias_artist), compact_text(alias_name))
        if key not in already_seen:
            already_seen.add(key)
            lookup_candidates.append((alias_name, alias_artist))

    # TODO : 검색어 증강(한국어 -> 영어, 일본어 등등 언어로 증강)이 중요한 것 같음. llm이 잘할 것 같은데.
    # TODO : DB에 계속해서 쌓아나가면 좋을 것 같다.
    for lookup_track_name, lookup_artist in lookup_candidates:
        try:
            # lastfm에서 비슷한 track search
            lf_track = lastfm.get_track(lookup_artist, lookup_track_name)
            raw = await _lf_call(
                f"lf:track_similar:{compact_text(lookup_artist)}:{compact_text(lookup_track_name)}:{limit}",
                600,
                lf_track.get_similar,
                limit,
            )

        except Exception as exc:
            last_error = exc
            logger.info(
                "[TrackSimilar] lookup failed for %s - %s: %s",
                lookup_artist,
                lookup_track_name,
                exc,
            )
            continue
        if raw:
            # alias를 이용한 추천 결과인 경우 info
            if (lookup_track_name, lookup_artist) != (track_name, artist):
                logger.info(
                    "[TrackSimilar] alias used: %s - %s -> %s - %s",
                    artist,
                    track_name,
                    lookup_artist,
                    lookup_track_name,
                )
            return list(raw)

    if last_error:
        logger.info("[TrackSimilar] no usable lookup after error: %s", last_error)
    return []


def tags_from_query(query: str) -> list[str]:
    """Map free-form genre/mood text to Last.fm tags without calling an LLM."""
    compact_query = compact_text(query)
    lowered_query = query.lower()
    tags: list[str] = []

    for keywords, rule_tags in _TAG_QUERY_RULES:
        if any(
            keyword.lower() in lowered_query or compact_text(keyword) in compact_query
            for keyword in keywords
        ):
            tags.extend(rule_tags)

    for keywords, rule_tags in _MOOD_QUERY_RULES:
        if any(
            keyword.lower() in lowered_query or compact_text(keyword) in compact_query
            for keyword in keywords
        ):
            tags.extend(rule_tags)

    return _unique_preserve_order(tags)[:8]


async def tag_based_recommendations(
    query: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n: int = 10,
) -> dict[str, list[TrackInfo]] | None:
    """Fallback for mood/genre queries such as '감성적인 시티팝'."""
    tags = tags_from_query(query)
    if not tags:
        return None

    primary_rows = await _collect_tag_tracks(tags, lastfm, limit=max(top_n * 5, 30))
    pool = _dedupe_tracks([track for rows in primary_rows for track in rows])
    if not pool:
        return None

    pool = await _enrich_metadata(
        http, _cap_per_artist(pool, max_per=3)[: max(top_n * 5, 40)]
    )
    pool = await _fill_lastfm_album_art(lastfm, pool)

    similar_candidates = _cap_per_artist(pool, max_per=2)
    similar_with_art = [track for track in similar_candidates if track.album_art_url]
    similar = (
        similar_with_art if len(similar_with_art) >= top_n else similar_candidates
    )[:top_n]
    for track in similar:
        tag = track.reason_tags[0] if track.reason_tags else tags[0]
        track.algo, track.label = "tag_similarity", f"#{tag} 태그 추천"
        track.reverse_score = track.match_score

    used_keys = {_track_key(track) for track in similar}
    reverse_pool = [track for track in pool if _track_key(track) not in used_keys]
    for index, track in enumerate(reverse_pool):
        popularity = track.popularity if track.popularity is not None else 55
        obscurity = max(0.0, min(1.0, (80 - popularity) / 80))
        rank_depth = min(1.0, index / max(len(reverse_pool) - 1, 1))
        tag_match = max(0.0, min(1.0, track.match_score or 0.0))
        track.reverse_score = (
            (obscurity * 0.55) + (rank_depth * 0.25) + (tag_match * 0.20)
        )

    low_exposure_pool = [
        track
        for track in reverse_pool
        if (track.popularity if track.popularity is not None else 55) < 70
    ]
    if len(low_exposure_pool) >= top_n:
        reverse_pool = low_exposure_pool
    reverse_candidates = sorted(
        reverse_pool, key=lambda item: item.reverse_score or 0, reverse=True
    )
    reverse_with_art = [track for track in reverse_candidates if track.album_art_url]
    reverse = (
        reverse_with_art if len(reverse_with_art) >= top_n else reverse_candidates
    )[:top_n]
    for track in reverse:
        track.algo, track.label = "tag_reverse", "태그 속 저노출곡"

    used_keys.update(_track_key(track) for track in reverse)
    opposite_tags = _opposite_tag_candidates(tags, "")
    opposite_rows = await _collect_tag_tracks(
        opposite_tags, lastfm, limit=max(top_n * 3, 20)
    )
    opposite_pool = _dedupe_tracks(
        [
            track
            for rows in opposite_rows
            for track in rows
            if _track_key(track) not in used_keys
        ]
    )
    opposite = await _enrich_metadata(
        http, _cap_per_artist(opposite_pool, max_per=1)[:top_n]
    )
    opposite = await _fill_lastfm_album_art(lastfm, opposite)
    for track in opposite:
        tag = track.reason_tags[0] if track.reason_tags else "contrast"
        track.algo, track.label = "tag_opposite", f"#{tag} 반대 결 추천"

    used_keys.update(_track_key(track) for track in opposite)
    hidden_pool = [track for track in pool if _track_key(track) not in used_keys]
    hidden_candidates = sorted(
        _cap_per_artist(hidden_pool, max_per=1),
        key=lambda item: item.popularity if item.popularity is not None else 55,
    )
    hidden_with_art = [track for track in hidden_candidates if track.album_art_url]
    hidden = (hidden_with_art if len(hidden_with_art) >= top_n else hidden_candidates)[
        :top_n
    ]
    for track in hidden:
        track.algo, track.label = "tag_hidden", "태그에서 더 파볼 곡"

    return {
        "similar": similar,
        "reverse": reverse,
        "opposite": opposite,
        "hidden": hidden,
    }


async def _collect_tag_tracks(
    tags: list[str],
    lastfm: pylast.LastFMNetwork,
    limit: int,
) -> list[list[TrackInfo]]:
    async def _fetch(tag: str, tag_index: int) -> list[TrackInfo]:
        try:
            tag_obj = lastfm.get_tag(tag)
            raw = await _lf_call(
                f"lf:tag_top:{compact_text(tag)}:{limit}",
                600,
                tag_obj.get_top_tracks,
                limit,
            )
        except Exception as exc:
            logger.warning("[TagFallback] tag.get_top_tracks failed (%s): %s", tag, exc)
            return []

        tracks: list[TrackInfo] = []
        for rank, item in enumerate(raw or []):
            try:
                name = str(item.item.get_name() or "").strip()
                artist = str(item.item.get_artist().get_name() or "").strip()
            except Exception:
                continue
            if not name or not artist:
                continue
            tag_priority = max(0.0, 1.0 - (tag_index * 0.08))
            rank_score = max(0.0, 1.0 - (rank / max(limit, 1)))
            tracks.append(
                TrackInfo(
                    name=name,
                    artist=artist,
                    match_score=tag_priority * 0.65 + rank_score * 0.35,
                    reason_tags=[tag],
                )
            )
        return tracks

    return list(
        await asyncio.gather(*[_fetch(tag, index) for index, tag in enumerate(tags)])
    )


async def _fill_lastfm_album_art(
    lastfm: pylast.LastFMNetwork,
    tracks: list[TrackInfo],
) -> list[TrackInfo]:
    missing = [t for t in tracks if not t.album_art_url]
    if not missing:
        return tracks

    async def _fetch(track: TrackInfo) -> None:
        try:
            lf_track_obj = lastfm.get_track(track.artist, track.name)
            artwork = await _lf_call(
                f"lf:cover:{compact_text(track.artist)}:{compact_text(track.name)}",
                600,
                lf_track_obj.get_cover_image,
            )
        except Exception:
            return
        if artwork:
            track.album_art_url = str(artwork)

    await asyncio.gather(*[_fetch(t) for t in missing])
    return tracks


async def reverse_top100(
    track_name, artist, http, lastfm, top_n=10, *, prefetched=None
) -> list[TrackInfo]:
    """Discover less-mainstream tracks via direct similarity (A) and similar-artist networks (B)."""
    try:
        # ── 소스 A + 유사 아티스트 목록 병렬 수집 ──────────────────
        if prefetched is not None:
            raw_similar_tracks = prefetched
        else:
            _limit = max(60, top_n * 6)
            raw_similar_tracks = await _track_similar_tracks(
                track_name, artist, lastfm, _limit
            )
        raw_similar_artists: list | Exception = []
        try:
            lf_artist = lastfm.get_artist(artist)
            _artist_limit = max(3, min(top_n, 8))
            raw_similar_artists = await _lf_call(
                f"lf:artist_similar:{compact_text(artist)}:{_artist_limit}",
                600,
                lf_artist.get_similar,
                _artist_limit,
            )
        except Exception as exc:
            logger.info("[Reverse] similar artist expansion unavailable: %s", exc)

        pool_a: list[TrackInfo] = []
        if not isinstance(raw_similar_tracks, Exception):
            pool_a = [
                TrackInfo(
                    name=item.item.get_name(),
                    artist=item.item.get_artist().get_name(),
                    match_score=float(item.match),
                )
                for item in raw_similar_tracks
            ]

        # ── 소스 B: 유사 아티스트 3명의 상위 20트랙 ─────────────
        similar_artists = (
            []
            if isinstance(raw_similar_artists, Exception)
            else [sa.item for sa in raw_similar_artists]
        )

        async def _fetch_artist_tracks(src, artist_rank: int) -> list[TrackInfo]:
            synthetic_match = max(0.3, 0.70 - (artist_rank - 1) * 0.1)
            try:
                src_name = compact_text(str(src.get_name()))
                raw = await _lf_call(
                    f"lf:artist_top:{src_name}:20",
                    600,
                    src.get_top_tracks,
                    20,
                )
                return [
                    TrackInfo(
                        name=item.item.get_name(),
                        artist=item.item.get_artist().get_name(),
                        match_score=synthetic_match,
                    )
                    for item in raw
                ]
            except Exception:
                return []

        b_results = await asyncio.gather(
            *[
                _fetch_artist_tracks(sa, rank)
                for rank, sa in enumerate(similar_artists, 1)
            ],
            return_exceptions=True,
        )
        pool_b: list[TrackInfo] = []
        for res in b_results:
            if not isinstance(res, Exception):
                pool_b.extend(res)

        # ── 병합 + 중복 제거 ──────────────────────────────────────
        input_key = (track_name.lower(), artist.lower())
        seen: set[str] = set()
        merged: list[TrackInfo] = []
        for t in pool_a + pool_b:
            if (t.name.lower(), t.artist.lower()) == input_key:
                continue
            key = _track_key(t)
            if key not in seen:
                seen.add(key)
                merged.append(t)

        logger.info(
            "[Reverse] 후보 A=%d B=%d 합계=%d", len(pool_a), len(pool_b), len(merged)
        )

        # match_score 상위 후보를 보강하되 특정 아티스트 쏠림은 줄인다.
        merged = _balanced_candidate_slice(merged, top_n * 3)
        merged = _dedupe_tracks(await _enrich_metadata(http, merged))
        merged = await _fill_lastfm_album_art(lastfm, merged)

        # ── 비주류 점수 계산 ───────────────────────────────────────
        obvious_keys = (
            {_track_key(t) for t in merged[:top_n]} if len(merged) > top_n else set()
        )
        discovery_pool = [t for t in merged if _track_key(t) not in obvious_keys]
        low_exposure = [
            t
            for t in discovery_pool
            if (t.popularity if t.popularity is not None else 55) < 70
        ]
        if len(low_exposure) >= top_n:
            discovery_pool = low_exposure

        pool_size = max(len(discovery_pool) - 1, 1)
        for index, t in enumerate(discovery_pool):
            popularity = t.popularity if t.popularity is not None else 55
            obscurity = max(0.0, min(1.0, (75 - popularity) / 75))
            match = max(0.0, min(1.0, t.match_score or 0.0))
            middle_similarity = max(0.0, 1 - (abs(match - 0.35) / 0.45))
            rank_novelty = index / pool_size
            t.reverse_score = (
                (obscurity * 0.55) + (middle_similarity * 0.30) + (rank_novelty * 0.15)
            )

        sorted_pool = sorted(
            discovery_pool, key=lambda t: t.reverse_score or 0, reverse=True
        )
        ranked = _diverse_top_n(
            _cap_per_artist(sorted_pool, max_per=2), top_n, diversity=0.0
        )
        ranked = _fill_from_ranked_pool(ranked, sorted_pool, top_n)
        for t in ranked:
            t.algo, t.label = "reverse_top100", "당신만 모르는 숨겨진 명곡"
        logger.info("[Reverse] 최종 선정 %d개", len(ranked))
        return ranked
    except Exception as exc:
        logger.warning("[reverse_top100] failed: %s", exc)
        return []


async def similar_listening_pattern(
    track_name, artist, http, lastfm, top_n=10, *, prefetched=None
) -> list[TrackInfo]:
    """Recommend tracks with the strongest Last.fm similarity."""
    try:
        if prefetched is not None:
            raw_similar = prefetched
        else:
            raw_similar = await _track_similar_tracks(track_name, artist, lastfm, 50)
        pool = _dedupe_tracks(
            [
                TrackInfo(
                    name=item.item.get_name(),
                    artist=item.item.get_artist().get_name(),
                    match_score=float(item.match),
                )
                for item in raw_similar
            ]
        )
        pool.sort(key=lambda t: t.match_score or 0, reverse=True)
        pool = pool[:top_n]
        pool = await _enrich_metadata(http, pool)
        pool = await _fill_lastfm_album_art(lastfm, pool)
        for track in pool:
            track.reverse_score = track.match_score or 0
            track.algo, track.label = (
                "similar_listening_pattern",
                "비슷한 취향의 사람들이 들어요",
            )
        return pool
    except Exception as exc:
        logger.warning("[similar_listening_pattern] failed: %s", exc)
        return []


async def opposite_emotion(
    track_name, artist, http, lastfm, top_n=10
) -> list[TrackInfo]:
    """Return mood/genre contrast recommendations with robust tag fallbacks."""
    try:
        seed_tags = await _seed_tags(track_name, artist, lastfm)
        query_tags = _opposite_tag_candidates(seed_tags, artist)
        collected: list[TrackInfo] = []

        for tag in query_tags:
            tag_obj = lastfm.get_tag(tag)
            raw = await _lf_call(
                f"lf:tag_top:{compact_text(tag)}:{top_n * 4}",
                600,
                tag_obj.get_top_tracks,
                top_n * 4,
            )
            for item in raw or []:
                name = item.item.get_name()
                item_artist = item.item.get_artist().get_name()
                if _is_same_track(name, item_artist, track_name, artist):
                    continue
                collected.append(
                    TrackInfo(
                        name=name,
                        artist=item_artist,
                        algo="opposite_emotion",
                        label=f"#{tag} 반전 무드",
                    )
                )
            collected = _cap_per_artist(_dedupe_tracks(collected), max_per=1)
            if len(collected) >= top_n:
                break

        if not collected:
            lf_track = lastfm.get_track(artist, track_name)
            raw_similar = await _lf_call(
                f"lf:track_similar:{compact_text(artist)}:{compact_text(track_name)}:{top_n * 4}",
                600,
                lf_track.get_similar,
                top_n * 4,
            )
            collected = [
                TrackInfo(
                    name=item.item.get_name(),
                    artist=item.item.get_artist().get_name(),
                    match_score=float(item.match),
                    algo="opposite_emotion",
                    label="유사곡 기반 반전 추천",
                )
                for item in raw_similar or []
            ]

        ranked = await _enrich_metadata(
            http, _cap_per_artist(_dedupe_tracks(collected), max_per=1)[:top_n]
        )
        ranked = await _fill_lastfm_album_art(lastfm, ranked)
        for track in ranked:
            track.algo = track.algo or "opposite_emotion"
            track.label = track.label or "반전 무드 추천"
        return ranked
    except Exception as exc:
        logger.warning("[opposite_emotion] failed: %s", exc)
        return []


async def hidden_discovery(
    track_name, artist, http, lastfm, top_n=10
) -> list[TrackInfo]:
    """Discover tracks by similar artists while excluding the seed artist."""
    try:
        seed_artist_key = compact_text(artist)
        lf_artist = lastfm.get_artist(artist)
        _hidden_limit = max(top_n * 3, 18)
        raw_artists = await _lf_call(
            f"lf:artist_similar:{compact_text(artist)}:{_hidden_limit}",
            600,
            lf_artist.get_similar,
            _hidden_limit,
        )
        artist_rows = []
        for artist_rank, item in enumerate(raw_artists or []):
            try:
                similar_artist = item.item
                similar_artist_name = str(similar_artist.get_name() or "").strip()
                artist_match = float(getattr(item, "match", 0) or 0)
            except Exception:
                continue
            if (
                not similar_artist_name
                or compact_text(similar_artist_name) == seed_artist_key
            ):
                continue
            artist_rows.append(
                (artist_rank, similar_artist, similar_artist_name, artist_match)
            )

        async def _fetch_artist_tracks(row):
            artist_rank, similar_artist, similar_artist_name, artist_match = row
            try:
                raw_tracks = await _lf_call(
                    f"lf:artist_top:{compact_text(similar_artist_name)}:4",
                    600,
                    similar_artist.get_top_tracks,
                    4,
                )
            except Exception as exc:
                logger.info(
                    "[hidden_discovery] similar artist tracks unavailable (%s): %s",
                    similar_artist_name,
                    exc,
                )
                return []

            rows = []
            for track_rank, item in enumerate(raw_tracks or [], start=1):
                try:
                    name = str(item.item.get_name() or "").strip()
                    item_artist = str(item.item.get_artist().get_name() or "").strip()
                except Exception:
                    continue
                if (
                    not name
                    or not item_artist
                    or compact_text(item_artist) == seed_artist_key
                ):
                    continue
                rows.append(
                    (
                        artist_rank,
                        track_rank,
                        artist_match,
                        TrackInfo(
                            name=name,
                            artist=item_artist,
                            match_score=artist_match,
                            reason_tags=[similar_artist_name],
                        ),
                    )
                )
            return rows

        fetched = await asyncio.gather(
            *[_fetch_artist_tracks(row) for row in artist_rows]
        )
        candidate_rows = [row for rows in fetched for row in rows]
        if not candidate_rows:
            return []

        # popularity 없이 artist_affinity + track_depth 사전 점수로 top_n*3 선별
        n_rows = max(len(artist_rows), 1)
        pre_scored = []
        for row in candidate_rows:
            artist_rank, track_rank, artist_match, _ = row
            affinity = (
                artist_match
                if artist_match > 0
                else max(0.0, 1 - (artist_rank / n_rows))
            )
            affinity = max(0.0, min(1.0, affinity))
            depth = min(1.0, max(0.0, (track_rank - 1) / 3))
            pre_scored.append((affinity * 0.55 + depth * 0.45, row))
        pre_scored.sort(key=lambda x: x[0], reverse=True)
        candidate_rows = [row for _, row in pre_scored[: top_n * 3]]

        tracks = await _enrich_metadata(http, [row[3] for row in candidate_rows])
        tracks = await _fill_lastfm_album_art(lastfm, tracks)
        for row, track in zip(candidate_rows, tracks):
            artist_rank, track_rank, artist_match, _ = row
            popularity = track.popularity if track.popularity is not None else 55
            obscurity = max(0.0, min(1.0, (80 - popularity) / 80))
            artist_affinity = (
                artist_match
                if artist_match > 0
                else max(0.0, 1 - (artist_rank / n_rows))
            )
            artist_affinity = max(0.0, min(1.0, artist_affinity))
            track_depth = min(1.0, max(0.0, (track_rank - 1) / 3))
            track.reverse_score = (
                (artist_affinity * 0.35) + (obscurity * 0.45) + (track_depth * 0.20)
            )

        ranked_pool = sorted(
            tracks, key=lambda item: item.reverse_score or 0, reverse=True
        )
        low_exposure_pool = [
            track
            for track in ranked_pool
            if (track.popularity if track.popularity is not None else 55) < 70
        ]
        if len(low_exposure_pool) >= top_n:
            ranked_pool = low_exposure_pool

        ranked = _cap_per_artist(_dedupe_tracks(ranked_pool), max_per=1)[:top_n]
        for track in ranked:
            track.algo, track.label = "hidden_discovery", "닮은 아티스트의 발견곡"
        return ranked
    except Exception as exc:
        logger.warning("[hidden_discovery] failed: %s", exc)
        return []


async def _seed_tags(
    track_name: str, artist: str, lastfm: pylast.LastFMNetwork
) -> list[str]:
    tags: list[str] = []
    try:
        lf_track = lastfm.get_track(artist, track_name)
        raw_tags = await _lf_call(
            f"lf:track_tags:{compact_text(artist)}:{compact_text(track_name)}",
            600,
            lf_track.get_top_tags,
        )
        tags.extend(_tag_names(raw_tags))
    except Exception as exc:
        logger.info("[opposite_emotion] track tags unavailable: %s", exc)

    if not tags:
        try:
            lf_artist = lastfm.get_artist(artist)
            raw_tags = await _lf_call(
                f"lf:artist_tags:{compact_text(artist)}",
                600,
                lf_artist.get_top_tags,
            )
            tags.extend(_tag_names(raw_tags))
        except Exception as exc:
            logger.info("[opposite_emotion] artist tags unavailable: %s", exc)

    if compact_text(artist) in {compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
        tags.extend(["k-pop", "pop", "female vocalists"])

    if not tags:
        tags.extend(["pop", "indie", "alternative"])

    return _unique_preserve_order(tags)[:8]


def _tag_names(raw_tags: Any) -> list[str]:
    names: list[str] = []
    for item in raw_tags or []:
        try:
            name = str(item.item.get_name() or "").strip().lower()
        except Exception:
            name = ""
        if name:
            names.append(name)
    return names


def _opposite_tag_candidates(seed_tags: list[str], artist: str) -> list[str]:
    candidates: list[str] = []
    for tag in seed_tags:
        normalized = tag.lower().replace("-", " ")
        for key, alternatives in _OPPOSITE_TAGS.items():
            if key.replace("-", " ") in normalized:
                candidates.extend(alternatives)

    if compact_text(artist) in {compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
        candidates.extend(["k-pop", "indie pop", "female vocalists"])

    candidates.extend(["pop", "indie", "alternative", "electronic"])
    return _unique_preserve_order(candidates)[:8]


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result


def _is_same_track(name: str, artist: str, seed_name: str, seed_artist: str) -> bool:
    return text_ratio(name, seed_name) > 0.88 and text_ratio(artist, seed_artist) > 0.75
