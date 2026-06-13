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
from typing import Any, Literal

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

    item = await _deezer_search(http, track_name, artist)
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


async def _deezer_search(
    http: httpx.AsyncClient, track_name: str, artist: str
) -> dict[str, Any] | None:
    if _is_dz_rate_limited():
        return None

    # dezeer에 search하기 전 track_name 전처리
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
        # TODO : Catalog 해체분석 해보기.
        result = await CatalogClient(http).deezer_search_best(track_name, artist)
    except DeezerRateLimitError as e:
        _mark_dz_rate_limited({"Retry-After": str(e.retry_after)})
        return None
    _cache_set(cache_key, result)
    return result


async def _get_tracks_metadata(
    http: httpx.AsyncClient,
    tracks: list[TrackInfo],
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
        # deezer/itunes에서 track을 검색하여 metadata를 가져옴
        deezer_item, itunes_item = await asyncio.gather(
            _deezer_search(http, track.name, track.artist),
            _itunes_search(http, track.name, track.artist, limit=8, min_score=0.45),
        )
        # popularity 추가
        if "popularity" in active_fields:
            if deezer_item:
                # rank : 해당 곡이 얼마나 많이 재생되었는지
                rank = int(deezer_item.get("rank") or 0)
            else:
                rank = 0
            if rank > 0:
                # rank 정규화
                track.popularity = min(100, int(math.log10(rank + 1) * 10))

        # album_art_url 추가
        if "album_art" in active_fields:
            if itunes_item:
                track.album_art_url = (
                    _itunes_artwork(itunes_item) or track.album_art_url
                )

            # itunes에 album art가 없는 경우 deezer에서 가져옴
            if not track.album_art_url and deezer_item:
                album = deezer_item.get("album") or {}
                track.album_art_url = (
                    album.get("cover_big")
                    or album.get("cover_medium")
                    or album.get("cover")
                )
            # TODO : 그래도 없는 경우는 lastfm에서?

        if "source_id" in active_fields:
            # source_id 추가
            itunes_id = _itunes_source_id(itunes_item) if itunes_item else None
            deezer_id = _deezer_source_id(deezer_item) if deezer_item else None
            # track id를 우선순위에 맞게 부여(itune > 기존 id > deezer)
            track.source_id = itunes_id or track.source_id or deezer_id

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
    """
    track list를 입력받아 중복되는 track을 제거하고 return합니다.
    """
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


async def _track_similar_tracks(
    track_name: str,
    artist: str,
    lastfm: pylast.LastFMNetwork,
    limit: int,
) -> list:
    """
    lasfm API를 사용하여 track_name과 비슷한 노래를 가져옴.
    """
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

    pool = await _get_tracks_metadata(
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
    opposite_tags = _get_opposite_tags(tags, "")
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
    opposite = await _get_tracks_metadata(
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


async def _fetch_artist_tracks(src, artist_rank: int) -> list[TrackInfo]:
    synthetic_match = max(0.3, 0.70 - (artist_rank - 1) * 0.1)
    similar_tracks = []
    src_name = compact_text(str(src.get_name()))
    response = await _lf_call(
        f"lf:artist_top:{src_name}:20",
        600,
        src.get_top_tracks,
        20,
    )

    for raw in response:
        similar_tracks.append(
            TrackInfo(
                name=raw.item.get_name(),
                artist=raw.item.get_artist().get_name(),
                match_score=synthetic_match,
            )
        )
    return similar_tracks


async def reverse_top100(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n=10,
    *,
    prefetched=None,
) -> list[TrackInfo]:
    """Discover less-mainstream tracks via direct similarity (A) and similar-artist networks (B)."""
    try:
        if prefetched is not None:
            similar_tracks = prefetched

        # NOTE : 이거 하는 이유가 없어보임. 차라리 track_similar_tracks에 retry 로직 걸어두기
        else:
            _limit = max(60, top_n * 6)
            similar_tracks = await _track_similar_tracks(
                track_name, artist, lastfm, _limit
            )

        # 소스 A : lasfm에서 가져온(pretetched) similar track
        pool_a: list[TrackInfo] = []
        if not isinstance(similar_tracks, Exception):
            pool_a = [
                TrackInfo(
                    name=item.item.get_name(),
                    artist=item.item.get_artist().get_name(),
                    match_score=float(item.match),
                )
                for item in similar_tracks
            ]

        # 소스 B : 유저가 검색한 아티스트와 유사한 아티스트 3명의 상위 20트랙

        # lastfm에서 user query의 artist와 유사한 artist를 가져옴
        similar_artists: list = []
        try:
            artist_lf_info = lastfm.get_artist(artist)
            artist_search_limit = max(3, min(top_n, 8))
            similar_artists = await _lf_call(
                f"lf:artist_similar:{compact_text(artist)}:{artist_search_limit}",
                600,
                artist_lf_info.get_similar,
                artist_search_limit,
            )

        except Exception as exc:
            logger.info("[Reverse] similar artist expansion unavailable: %s", exc)

        similar_artists = [sa.item for sa in similar_artists]

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
        merged = _dedupe_tracks(await _get_tracks_metadata(http, merged))
        merged = await _fill_lastfm_album_art(lastfm, merged)

        # ── 비주류 점수 계산 ───────────────────────────────────────
        # 1단계: 너무 뻔한 상위 추천곡 제외하기 (Obvious Filter)
        obvious_keys = set()

        # 추천 리스트가 요구하는 개수(top_n)보다 충분히 많을 때만 상위 곡을 걸러냅니다.
        if len(merged) > top_n:
            for track in merged[:top_n]:
                obvious_keys.add(_track_key(track))

        # 뻔한 곡 목록에 없는 곡들만 새로운 풀에 담습니다.
        discovery_pool = []
        for track in merged:
            key = _track_key(track)
            if key not in obvious_keys:
                discovery_pool.append(track)
        # TODO : 상수 관리할 방법 고민. 따로 파일에 빼서 관리할 까 싶기도 함
        DEFAULT_POPULARITY = 55
        LOW_EXPOUSURE_CUT = 70
        MAX_POPULARITY = 75
        # 인기도가 낮은 곡들만 추가
        low_exposure = []
        for track in discovery_pool:
            if track.popularity is None:
                track.popularity = DEFAULT_POPULARITY

            if track.popularity < LOW_EXPOUSURE_CUT:
                low_exposure.append(track)

        # 만약, 비주류 곡들이 추천 갯수보다 많다면 유명한 곡들은 완전히 제거하고 비주류 곡만 pool에 담음
        if len(low_exposure) >= top_n:
            discovery_pool = low_exposure

        pool_size = max(len(discovery_pool) - 1, 1)
        # 비주류 점수 계산
        for index, t in enumerate(discovery_pool):
            popularity = t.popularity
            obscurity = max(
                0.0,
                min(
                    1.0,
                    (MAX_POPULARITY - popularity) / MAX_POPULARITY,
                ),
            )
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
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n=10,
    *,
    prefetched=None,
) -> list[TrackInfo]:
    """Recommend tracks with the strongest Last.fm similarity."""
    try:
        if prefetched is not None:
            raw_similar = prefetched
        else:
            # NOTE : 이거 하는 이유가? error 발생해서 None이 넘어올건데 2중으로 call하는 이유는 없어보임
            raw_similar = await _track_similar_tracks(track_name, artist, lastfm, 50)

        # 중복 제거
        raw_tracks = [
            TrackInfo(
                name=item.item.get_name(),
                artist=item.item.get_artist().get_name(),
                match_score=float(item.match),
            )
            for item in raw_similar
        ]
        unique_tracks = _dedupe_tracks(raw_tracks)

        # score 기준으로 정렬
        sorted_tracks = sorted(
            unique_tracks, key=lambda t: t.match_score or 0, reverse=True
        )
        top_tracks = sorted_tracks[:top_n]
        top_tracks = await _get_tracks_metadata(http, top_tracks)
        top_tracks = await _fill_lastfm_album_art(lastfm, top_tracks)
        for track in top_tracks:
            track.reverse_score = track.match_score or 0
            track.algo, track.label = (
                "similar_listening_pattern",
                "비슷한 취향의 사람들이 들어요",
            )
        return top_tracks
    except Exception as exc:
        logger.warning("[similar_listening_pattern] failed: %s", exc)
        return []


async def opposite_emotion(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n=10,
) -> list[TrackInfo]:
    """Return mood/genre contrast recommendations with robust tag fallbacks."""
    MAX_TAG_CNT = 8
    try:
        # 주어진 track과 artist의 tag를 추출하고 이에 반대되는 opposite tag 추출
        seed_tags = await _seed_tags(track_name, artist, lastfm)
        seed_tags = seed_tags[:MAX_TAG_CNT]
        opp_tags = _get_opposite_tags(seed_tags, artist)
        opp_tags = opp_tags[:MAX_TAG_CNT]
        collected: list[TrackInfo] = []

        # seed tag와 반대 속성의 tag의 곡들 중에서 인기도 순으로 lastfm에서 검색
        for tag in opp_tags:
            tag_obj = lastfm.get_tag(tag)
            response = await _lf_call(
                f"lf:tag_top:{compact_text(tag)}:{top_n * 4}",
                600,
                tag_obj.get_top_tracks,
                top_n * 4,
            )
            for track_metadata in response or []:
                opp_track_name = track_metadata.item.get_name()
                opp_artist = track_metadata.item.get_artist().get_name()
                if _is_same_track(opp_track_name, opp_artist, track_name, artist):
                    continue
                collected.append(
                    TrackInfo(
                        name=opp_track_name,
                        artist=opp_artist,
                        algo="opposite_emotion",
                        label=f"#{tag} 반전 무드",
                    )
                )
            collected = _cap_per_artist(_dedupe_tracks(collected), max_per=1)
            if len(collected) >= top_n:
                break

        # NOTE : get_similar로 검색하고 reverse 해야하는거 아닌지? 수정 필요해 보임
        if not collected:
            lf_track = lastfm.get_track(artist, track_name)

            response = await _lf_call(
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
                for item in response or []
            ]
            collected = _cap_per_artist(_dedupe_tracks(collected), max_per=1)
        collected = collected[:top_n]
        opp_emotion_tracks = await _get_tracks_metadata(http, collected)

        # NOTE : enrich_metadata에서 이미 album_art_url이 채워짐.
        # opp_emotion_tracks = await _fill_lastfm_album_art(lastfm, opp_emotion_tracks)
        for track in opp_emotion_tracks:
            track.algo = track.algo or "opposite_emotion"
            track.label = track.label or "반전 무드 추천"
        return opp_emotion_tracks

    except Exception as exc:
        logger.warning("[opposite_emotion] failed: %s", exc)
        return []


async def hidden_discovery_by_artist(
    artist: str, http: httpx.AsyncClient, lastfm: pylast.LastFMNetwork, top_n=10
) -> list[TrackInfo]:
    """유저가 입력한 artist와 유사한 artist의 숨은 명곡을 추천하는 함수"""

    HIDDEN_LIMIT = max(top_n * 3, 18)
    try:
        # lastfm에서 유저가 검색한 artist와 유사한 artist를 가져옴
        seed_artist = compact_text(artist)
        lf_artist = lastfm.get_artist(artist)
        response_artists = await _lf_call(
            f"lf:artist_similar:{seed_artist}:{HIDDEN_LIMIT}",
            600,
            lf_artist.get_similar,
            HIDDEN_LIMIT,
        )
        artist_candidates = []

        for artist_rank, artist_metadata in enumerate(response_artists or []):
            try:
                similar_artist = artist_metadata.item
                similar_artist_name = str(similar_artist.get_name() or "").strip()
                artist_match = float(getattr(artist_metadata, "match", 0) or 0)
            except Exception as exc:
                logger.warning(
                    "[hidden_discovery] fail to get similar artist & score (%s): %s",
                    similar_artist_name,
                    exc,
                )
                continue

            if (
                not similar_artist_name
                or compact_text(similar_artist_name) == seed_artist
            ):
                continue
            artist_candidates.append(
                (artist_rank, similar_artist, similar_artist_name, artist_match)
            )

        async def _fetch_artist_tracks(artist_infos, max_track_num=4):
            artist_rank, pylast_artist, artist_name, artist_match = artist_infos

            # artist의 노래를 lastfm에서 가져옴
            try:
                response_tracks = await _lf_call(
                    f"lf:artist_top:{compact_text(artist_name)}:{max_track_num}",
                    600,
                    pylast_artist.get_top_tracks,
                    max_track_num,
                )
            except Exception as exc:
                logger.warning(
                    "[hidden_discovery] similar artist tracks unavailable (%s): %s",
                    artist_name,
                    exc,
                )
                return []

            # artist의 대표곡들 이름을 가져옴
            results = []
            for track_rank, track_metadata in enumerate(response_tracks or [], start=1):
                try:
                    track_name = str(track_metadata.item.get_name() or "").strip()
                except Exception as exc:
                    logger.warning(
                        "[hidden_discovery] fail to get track info (%s): %s",
                        artist_name,
                        exc,
                    )
                    continue

                if not track_name:
                    continue
                results.append(
                    (
                        artist_rank,
                        track_rank,
                        artist_match,
                        TrackInfo(
                            name=track_name,
                            artist=artist_name,
                            match_score=artist_match,
                            reason_tags=[artist_name],
                        ),
                    )
                )
            return results

        fetched = await asyncio.gather(
            *[_fetch_artist_tracks(row) for row in artist_candidates]
        )
        candidate_tracks = [row for rows in fetched for row in rows]
        if not candidate_tracks:
            return []

        candidates_len = max(len(artist_candidates), 1)
        pre_scored = []

        # 1차 비주류 점수 계산(사전 선별 top_n*3)
        for track_info in candidate_tracks:
            artist_rank, track_rank, artist_match, _ = track_info
            # 아티스트 유사도 & 트랙 depth 기준으로 정렬
            affinity = (
                artist_match
                if artist_match > 0
                else max(0.0, 1 - (artist_rank / candidates_len))
            )
            affinity = max(0.0, min(1.0, affinity))
            depth = min(1.0, max(0.0, (track_rank - 1) / 3))

            # 아티스트 유사도가 높을수록, 대표곡이 아닐수록(depth가 깊을수록) 점수가 높음
            pre_scored.append((affinity * 0.55 + depth * 0.45, track_info))
        pre_scored.sort(key=lambda x: x[0], reverse=True)
        prescored_candidates = [row for _, row in pre_scored[: top_n * 3]]

        tracks_metadata = await _get_tracks_metadata(
            http, [row[3] for row in prescored_candidates]
        )

        # 최종 비주류 점수 계산
        for candidate_score, track in zip(prescored_candidates, tracks_metadata):
            artist_rank, track_rank, artist_match, _ = candidate_score
            # 노래 재생횟수도 반영하여 비주류 점수 재계산
            popularity = track.popularity if track.popularity is not None else 55
            obscurity = max(0.0, min(1.0, (80 - popularity) / 80))
            artist_affinity = (
                artist_match
                if artist_match > 0
                else max(0.0, 1 - (artist_rank / candidates_len))
            )
            artist_affinity = max(0.0, min(1.0, artist_affinity))
            track_depth = min(1.0, max(0.0, (track_rank - 1) / 3))
            track.reverse_score = (
                (artist_affinity * 0.35) + (obscurity * 0.45) + (track_depth * 0.20)
            )

        # 노출도가 낮은 track을 우선적으로 추천
        ranked_pool = sorted(
            tracks_metadata, key=lambda item: item.reverse_score or 0, reverse=True
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
    """
    lastfm에서 track의 tag를 검색합니다.
    만약, track의 검색된 tag가 없는 경우 artist의 tag를 가져옵니다.
    """
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

    # if compact_text(artist) in {compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
    #    tags.extend(["k-pop", "pop", "female vocalists"])

    # if not tags:
    #    tags.extend(["pop", "indie", "alternative"])

    return _unique_preserve_order(tags)


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


def _get_opposite_tags(seed_tags: list[str], artist: str) -> list[str]:
    """
    seed tag를 받아 해당 tag의 속성과 반대 속성의 tag
    """
    candidates: list[str] = []

    # TODO : 미리 oppsite tag 설정도 좋지만 LLM으로 바꾸는 게 좋은듯
    for tag in seed_tags:
        normalized = tag.lower().replace("-", " ")
        for key, alternatives in _OPPOSITE_TAGS.items():
            if key.replace("-", " ") in normalized:
                candidates.extend(alternatives)

    # if compact_text(artist) in {compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
    #   candidates.extend(["k-pop", "indie pop", "female vocalists"])

    # candidates.extend(["pop", "indie", "alternative", "electronic"])
    return _unique_preserve_order(candidates)


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
