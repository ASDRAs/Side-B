import asyncio
import logging

import pylast

from app.config.rules import MOOD_QUERY_RULES as _MOOD_QUERY_RULES
from app.utils.text import compact_text
from recommend_algo.common import sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)

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
            raw = await sources._lf_call(
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


async def _collect_tag_tracks(
    tags: list[str],
    lastfm: pylast.LastFMNetwork,
    limit: int,
) -> list[list[TrackInfo]]:
    async def _fetch(tag: str, tag_index: int) -> list[TrackInfo]:
        try:
            tag_obj = lastfm.get_tag(tag)
            raw = await sources._lf_call(
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
