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
from difflib import SequenceMatcher
from typing import Any

import httpx
import pylast

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


_BAD_VERSION_MARKERS = (
    "karaoke",
    "instrumental",
    "instrumental karaoke",
    "inst.",
    "originally performed",
    "originally perfomed",
    "tribute",
    "cover",
    "cover version",
    "sped up",
    "slowed",
    "nightcore",
    "musicmaru",
    "뮤직마루",
    "노래방",
    "반주",
)

_QUERY_ALIASES = (
    (("윤하", "사건의지평선"), "Event Horizon", "Younha"),
)

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
    (("시티팝", "citypop", "city pop"), ["korean city pop", "citypop", "japanese city pop", "city pop"]),
    (("케이팝", "kpop", "k-pop", "아이돌"), ["k-pop", "dance-pop", "korean"]),
    (("발라드", "ballad"), ["k-ballad", "ballad", "korean"]),
    (("인디", "indie"), ["indie", "indie pop", "k-indie"]),
    (("알앤비", "rnb", "r&b"), ["rnb", "soul", "k-rnb"]),
    (("재즈", "jazz"), ["jazz", "vocal jazz"]),
    (("락", "록", "rock"), ["rock", "alternative"]),
    (("로파이", "lofi", "lo-fi"), ["lo-fi", "chill", "instrumental"]),
)

_MOOD_QUERY_RULES = (
    (("감성", "감성적", "emotional"), ["emotional", "chill"]),
    (("잔잔", "차분", "calm"), ["calm", "chill"]),
    (("신나는", "신나", "energetic", "upbeat"), ["upbeat", "dance"]),
    (("새벽", "밤", "late night"), ["late-night", "chill"]),
    (("몽환", "dreamy"), ["dreamy", "synth-pop"]),
    (("집중", "공부", "코딩", "focus", "study"), ["focus", "instrumental"]),
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


async def normalize_input(
    query: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
) -> tuple[str | None, str | None, str | None]:
    """Resolve a free-form query to a canonical track title and artist."""
    alias = _known_query_alias(query)
    if alias:
        alias_name, alias_artist = alias
        item = await _itunes_search(http, alias_name, alias_artist, limit=8, min_score=0.65)
        if item:
            name = str(item.get("trackName") or "").strip()
            artist = str(item.get("artistName") or "").strip()
            source_id = _itunes_source_id(item)
            if name and artist:
                logger.info("[Normalize] known alias via iTunes: %s - %s", name, artist)
                return name, artist, source_id

        logger.info("[Normalize] known alias fallback: %s - %s", alias_name, alias_artist)
        return alias_name, alias_artist, None

    item = await _itunes_search(http, query, limit=8, min_score=0.35)
    if item:
        name = str(item.get("trackName") or "").strip()
        artist = str(item.get("artistName") or "").strip()
        source_id = _itunes_source_id(item)
        if name and artist:
            logger.info("[Normalize] iTunes: %s - %s", name, artist)
            return name, artist, source_id

    try:
        search = lastfm.search_for_track("", query)
        results = await asyncio.to_thread(search.get_next_page)
        for track in results or []:
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
        artwork = album.get("cover_big") or album.get("cover_medium") or album.get("cover")
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
    term = f"{track_name} {artist}".strip()
    if not term:
        return None

    try:
        async with _API_SEMAPHORE:
            response = await http.get(
                ITUNES_URL,
                params={"term": term, "entity": "song", "limit": max(1, min(limit, 25))},
                timeout=5.0,
            )
            response.raise_for_status()
        results = response.json().get("results", [])
    except Exception as exc:
        logger.warning("[iTunes] search failed for %r: %s", term, exc)
        return None

    best: tuple[float, dict[str, Any]] | None = None
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("trackName") or "")
        item_artist = str(item.get("artistName") or "")
        if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
            continue
        score = _catalog_match_score(
            title,
            item_artist,
            track_name,
            artist,
        )
        if best is None or score > best[0]:
            best = (score, item)

    if not best or best[0] < min_score:
        return None
    return best[1]


async def _dz_search(http: httpx.AsyncClient, track_name: str, artist: str) -> dict[str, Any] | None:
    if _is_dz_rate_limited():
        return None

    clean_name = _clean_title(track_name)
    queries = [
        f'track:"{clean_name}" artist:"{artist}"',
        f"{clean_name} {artist}".strip(),
        clean_name,  # 3차 폴백: 곡명만
    ]

    for query in queries:
        if _is_dz_rate_limited():
            return None
        try:
            async with _API_SEMAPHORE:
                response = await http.get(
                    f"{DEEZER_URL}/search",
                    params={"q": query},
                    timeout=8.0,
                )
            if response.status_code == 429:
                _mark_dz_rate_limited(dict(response.headers))
                return None
            items = response.json().get("data", [])
        except Exception as exc:
            logger.warning("[Deezer] 요청 실패 (%s) q=%r — %s", type(exc).__name__, query[:70], exc)
            continue

        best = _select_deezer_item(items, clean_name, artist)
        if best:
            return best
    return None


async def _enrich_metadata(http: httpx.AsyncClient, tracks: list[TrackInfo]) -> list[TrackInfo]:
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
                album.get("cover_big") or album.get("cover_medium") or album.get("cover")
            )

        return track

    return list(await asyncio.gather(*[_fetch(t) for t in tracks]))


def _select_deezer_item(items: Any, track_name: str, artist: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None

    best: tuple[float, dict[str, Any]] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        item_artist = str((item.get("artist") or {}).get("name") or "")
        if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
            continue
        score = _catalog_match_score(title, item_artist, track_name, artist)
        artist_score = _text_ratio(item_artist, artist) if artist else 1.0
        if artist and artist_score < 0.52:
            continue
        if best is None or score > best[0]:
            best = (score, item)

    if not best or best[0] < 0.72:
        return None
    return best[1]


def _catalog_match_score(title: str, artist: str, expected_title: str, expected_artist: str = "") -> float:
    title_score = _text_ratio(_clean_title(title), _clean_title(expected_title))
    artist_score = _text_ratio(artist, expected_artist) if expected_artist else 1.0
    return (title_score * 0.68) + (artist_score * 0.32)


def _text_ratio(left: str, right: str) -> float:
    left_norm = _compact_text(left)
    right_norm = _compact_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.9
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _compact_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣ぁ-ゟ゠-ヿ一-鿿]+", "", value.lower())


def _clean_title(value: str) -> str:
    cleaned = re.sub(r"\(.*?\)|\[.*?\]", " ", value)
    cleaned = re.sub(r"\s+-\s+(remaster(?:ed)?|live|radio edit|single version).*$", " ", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()


def _looks_like_bad_version(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _BAD_VERSION_MARKERS)


def _known_query_alias(query: str) -> tuple[str, str] | None:
    compact_query = _compact_text(query)
    for tokens, title, artist in _QUERY_ALIASES:
        if all(_compact_text(token) in compact_query for token in tokens):
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
    return f"{_compact_text(track.artist)}::{_compact_text(track.name)}"


def tags_from_query(query: str) -> list[str]:
    """Map free-form genre/mood text to Last.fm tags without calling an LLM."""
    compact_query = _compact_text(query)
    lowered_query = query.lower()
    tags: list[str] = []

    for keywords, rule_tags in _TAG_QUERY_RULES:
        if any(keyword.lower() in lowered_query or _compact_text(keyword) in compact_query for keyword in keywords):
            tags.extend(rule_tags)

    for keywords, rule_tags in _MOOD_QUERY_RULES:
        if any(keyword.lower() in lowered_query or _compact_text(keyword) in compact_query for keyword in keywords):
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

    pool = await _enrich_metadata(http, _cap_per_artist(pool, max_per=3)[: max(top_n * 5, 40)])
    pool = await _fill_lastfm_album_art(lastfm, pool)

    similar_candidates = _cap_per_artist(pool, max_per=2)
    similar_with_art = [track for track in similar_candidates if track.album_art_url]
    similar = (similar_with_art if len(similar_with_art) >= top_n else similar_candidates)[:top_n]
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
        track.reverse_score = (obscurity * 0.55) + (rank_depth * 0.25) + (tag_match * 0.20)

    low_exposure_pool = [
        track for track in reverse_pool if (track.popularity if track.popularity is not None else 55) < 70
    ]
    if len(low_exposure_pool) >= top_n:
        reverse_pool = low_exposure_pool
    reverse_candidates = sorted(reverse_pool, key=lambda item: item.reverse_score or 0, reverse=True)
    reverse_with_art = [track for track in reverse_candidates if track.album_art_url]
    reverse = (reverse_with_art if len(reverse_with_art) >= top_n else reverse_candidates)[:top_n]
    for track in reverse:
        track.algo, track.label = "tag_reverse", "태그 속 저노출곡"

    used_keys.update(_track_key(track) for track in reverse)
    opposite_tags = _opposite_tag_candidates(tags, "")
    opposite_rows = await _collect_tag_tracks(opposite_tags, lastfm, limit=max(top_n * 3, 20))
    opposite_pool = _dedupe_tracks(
        [track for rows in opposite_rows for track in rows if _track_key(track) not in used_keys]
    )
    opposite = await _enrich_metadata(http, _cap_per_artist(opposite_pool, max_per=1)[:top_n])
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
    hidden = (hidden_with_art if len(hidden_with_art) >= top_n else hidden_candidates)[:top_n]
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
            raw = await asyncio.to_thread(lastfm.get_tag(tag).get_top_tracks, limit=limit)
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

    return list(await asyncio.gather(*[_fetch(tag, index) for index, tag in enumerate(tags)]))


async def _fill_lastfm_album_art(
    lastfm: pylast.LastFMNetwork,
    tracks: list[TrackInfo],
) -> list[TrackInfo]:
    async def _fetch(track: TrackInfo) -> TrackInfo:
        if track.album_art_url:
            return track
        try:
            async with _API_SEMAPHORE:
                artwork = await asyncio.to_thread(
                    lastfm.get_track(track.artist, track.name).get_cover_image
                )
        except Exception:
            return track
        if artwork:
            track.album_art_url = str(artwork)
        return track

    return list(await asyncio.gather(*[_fetch(track) for track in tracks]))


async def reverse_top100(track_name, artist, http, lastfm, top_n=10) -> list[TrackInfo]:
    """Discover less-mainstream tracks via direct similarity (A) and similar-artist networks (B)."""
    try:
        lf_track = lastfm.get_track(artist, track_name)

        # ── 소스 A + 유사 아티스트 목록 병렬 수집 ──────────────────
        raw_similar_tracks = await asyncio.to_thread(
            lf_track.get_similar,
            limit=max(60, top_n * 6),
        )
        raw_similar_artists: list | Exception = []
        try:
            lf_artist = lastfm.get_artist(artist)
            raw_similar_artists = await asyncio.to_thread(lf_artist.get_similar, limit=3)
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
            [] if isinstance(raw_similar_artists, Exception)
            else [sa.item for sa in raw_similar_artists]
        )

        async def _fetch_artist_tracks(src, artist_rank: int) -> list[TrackInfo]:
            synthetic_match = max(0.3, 0.70 - (artist_rank - 1) * 0.1)
            try:
                raw = await asyncio.to_thread(src.get_top_tracks, limit=20)
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
            *[_fetch_artist_tracks(sa, rank) for rank, sa in enumerate(similar_artists, 1)],
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

        logger.info("[Reverse] 후보 A=%d B=%d 합계=%d", len(pool_a), len(pool_b), len(merged))

        # match_score 상위 top_n×8만 보강 (Deezer 호출 최소화)
        merged.sort(key=lambda t: t.match_score or 0, reverse=True)
        merged = merged[: top_n * 8]
        merged = _dedupe_tracks(await _enrich_metadata(http, merged))
        merged = await _fill_lastfm_album_art(lastfm, merged)

        # ── 비주류 점수 계산 ───────────────────────────────────────
        obvious_keys = {_track_key(t) for t in merged[:top_n]} if len(merged) > top_n else set()
        discovery_pool = [t for t in merged if _track_key(t) not in obvious_keys]
        low_exposure = [
            t for t in discovery_pool
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
            t.reverse_score = (obscurity * 0.55) + (middle_similarity * 0.30) + (rank_novelty * 0.15)

        sorted_pool = sorted(discovery_pool, key=lambda t: t.reverse_score or 0, reverse=True)
        ranked = _diverse_top_n(_cap_per_artist(sorted_pool, max_per=2), top_n)
        for t in ranked:
            t.algo, t.label = "reverse_top100", "당신만 모르는 숨겨진 명곡"
        logger.info("[Reverse] 최종 선정 %d개", len(ranked))
        return ranked
    except Exception as exc:
        logger.warning("[reverse_top100] failed: %s", exc)
        return []


async def similar_listening_pattern(track_name, artist, http, lastfm, top_n=10) -> list[TrackInfo]:
    """Recommend tracks with the strongest Last.fm similarity."""
    try:
        lf_track = lastfm.get_track(artist, track_name)
        raw_similar = await asyncio.to_thread(lf_track.get_similar, limit=50)
        pool = [
            TrackInfo(
                name=item.item.get_name(),
                artist=item.item.get_artist().get_name(),
                match_score=float(item.match),
            )
            for item in raw_similar
        ]
        pool = _dedupe_tracks(await _enrich_metadata(http, pool))
        pool = await _fill_lastfm_album_art(lastfm, pool)
        for track in pool:
            track.reverse_score = track.match_score or 0
        ranked = sorted(pool, key=lambda item: item.reverse_score or 0, reverse=True)[:top_n]
        for track in ranked:
            track.algo, track.label = "similar_listening_pattern", "비슷한 취향의 사람들이 들어요"
        return ranked
    except Exception as exc:
        logger.warning("[similar_listening_pattern] failed: %s", exc)
        return []


async def opposite_emotion(track_name, artist, http, lastfm, top_n=10) -> list[TrackInfo]:
    """Return mood/genre contrast recommendations with robust tag fallbacks."""
    try:
        seed_tags = await _seed_tags(track_name, artist, lastfm)
        query_tags = _opposite_tag_candidates(seed_tags, artist)
        collected: list[TrackInfo] = []

        for tag in query_tags:
            raw = await asyncio.to_thread(lastfm.get_tag(tag).get_top_tracks, limit=top_n * 4)
            for item in raw or []:
                name = item.item.get_name()
                item_artist = item.item.get_artist().get_name()
                if _is_same_track(name, item_artist, track_name, artist):
                    continue
                collected.append(
                    TrackInfo(name=name, artist=item_artist, algo="opposite_emotion", label=f"#{tag} 반전 무드")
                )
            collected = _cap_per_artist(_dedupe_tracks(collected), max_per=1)
            if len(collected) >= top_n:
                break

        if not collected:
            lf_track = lastfm.get_track(artist, track_name)
            raw_similar = await asyncio.to_thread(lf_track.get_similar, limit=top_n * 4)
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

        ranked = await _enrich_metadata(http, _cap_per_artist(_dedupe_tracks(collected), max_per=1)[:top_n])
        ranked = await _fill_lastfm_album_art(lastfm, ranked)
        for track in ranked:
            track.algo = track.algo or "opposite_emotion"
            track.label = track.label or "반전 무드 추천"
        return ranked
    except Exception as exc:
        logger.warning("[opposite_emotion] failed: %s", exc)
        return []


async def hidden_discovery(track_name, artist, http, lastfm, top_n=10) -> list[TrackInfo]:
    """Discover tracks by similar artists while excluding the seed artist."""
    try:
        seed_artist_key = _compact_text(artist)
        lf_artist = lastfm.get_artist(artist)
        raw_artists = await asyncio.to_thread(lf_artist.get_similar, limit=max(top_n * 3, 18))
        artist_rows = []
        for artist_rank, item in enumerate(raw_artists or []):
            try:
                similar_artist = item.item
                similar_artist_name = str(similar_artist.get_name() or "").strip()
                artist_match = float(getattr(item, "match", 0) or 0)
            except Exception:
                continue
            if not similar_artist_name or _compact_text(similar_artist_name) == seed_artist_key:
                continue
            artist_rows.append((artist_rank, similar_artist, similar_artist_name, artist_match))

        async def _fetch_artist_tracks(row):
            artist_rank, similar_artist, similar_artist_name, artist_match = row
            try:
                raw_tracks = await asyncio.to_thread(similar_artist.get_top_tracks, limit=4)
            except Exception as exc:
                logger.info("[hidden_discovery] similar artist tracks unavailable (%s): %s", similar_artist_name, exc)
                return []

            rows = []
            for track_rank, item in enumerate(raw_tracks or [], start=1):
                try:
                    name = str(item.item.get_name() or "").strip()
                    item_artist = str(item.item.get_artist().get_name() or "").strip()
                except Exception:
                    continue
                if not name or not item_artist or _compact_text(item_artist) == seed_artist_key:
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

        fetched = await asyncio.gather(*[_fetch_artist_tracks(row) for row in artist_rows])
        candidate_rows = [row for rows in fetched for row in rows]
        if not candidate_rows:
            return []

        tracks = await _enrich_metadata(http, [row[3] for row in candidate_rows])
        tracks = await _fill_lastfm_album_art(lastfm, tracks)
        for row, track in zip(candidate_rows, tracks):
            artist_rank, track_rank, artist_match, _ = row
            popularity = track.popularity if track.popularity is not None else 55
            obscurity = max(0.0, min(1.0, (80 - popularity) / 80))
            artist_affinity = artist_match if artist_match > 0 else max(0.0, 1 - (artist_rank / max(len(artist_rows), 1)))
            artist_affinity = max(0.0, min(1.0, artist_affinity))
            track_depth = min(1.0, max(0.0, (track_rank - 1) / 3))
            track.reverse_score = (artist_affinity * 0.35) + (obscurity * 0.45) + (track_depth * 0.20)

        ranked_pool = sorted(tracks, key=lambda item: item.reverse_score or 0, reverse=True)
        low_exposure_pool = [
            track for track in ranked_pool if (track.popularity if track.popularity is not None else 55) < 70
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


async def _seed_tags(track_name: str, artist: str, lastfm: pylast.LastFMNetwork) -> list[str]:
    tags: list[str] = []
    try:
        lf_track = lastfm.get_track(artist, track_name)
        raw_tags = await asyncio.to_thread(lf_track.get_top_tags)
        tags.extend(_tag_names(raw_tags))
    except Exception as exc:
        logger.info("[opposite_emotion] track tags unavailable: %s", exc)

    if not tags:
        try:
            lf_artist = lastfm.get_artist(artist)
            raw_tags = await asyncio.to_thread(lf_artist.get_top_tags)
            tags.extend(_tag_names(raw_tags))
        except Exception as exc:
            logger.info("[opposite_emotion] artist tags unavailable: %s", exc)

    if _compact_text(artist) in {_compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
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

    if _compact_text(artist) in {_compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
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
    return _text_ratio(name, seed_name) > 0.88 and _text_ratio(artist, seed_artist) > 0.75
