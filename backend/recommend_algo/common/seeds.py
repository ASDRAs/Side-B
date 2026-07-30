import asyncio
import logging

import pylast

from app.utils.text import compact_text
from recommend_algo.common import scoring, sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)

_TRACK_SIMILAR_ALIASES = {
    (compact_text("Younha"), compact_text("혜성")): [("ほうき星", "Younha")],
    (compact_text("IU"), compact_text("너랑 나 (YOU&I)")): [
        ("You & I", "IU"),
        ("You&I", "IU"),
    ],
    (compact_text("IU"), compact_text("너랑나")): [
        ("You & I", "IU"),
        ("You&I", "IU"),
    ],
}


def _lookup_key(artist: str, track_name: str) -> tuple[str, str]:
    """Last.fm 조회 후보를 구분하는 키.

    alias 테이블 조회에는 compact_text를 계속 쓴다. 들어오는 곡명의 문장부호가
    어떻든 테이블에 걸려야 하기 때문이다. 반면 조회 후보끼리 구분할 때는
    문장부호를 살려야 한다. Last.fm이 "You & I"와 "You&I"를 다른 트랙으로 보고
    전자는 0개, 후자는 5개를 돌려주기 때문이다.
    """
    return (artist.strip().lower(), track_name.strip().lower())


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

    # 중복 제거. compact_text를 쓰면 안 된다 — &와 공백을 지워 "You & I"와 "You&I"가
    # 같은 키가 되고 뒤엣것이 사라진다. Last.fm은 둘을 다른 트랙으로 취급하며
    # 실제로 유사곡이 나오는 쪽이 "You&I"다.
    already_seen = {_lookup_key(artist, track_name)}
    for alias_name, alias_artist in aliases:
        key = _lookup_key(alias_artist, alias_name)
        if key not in already_seen:
            already_seen.add(key)
            lookup_candidates.append((alias_name, alias_artist))

    # TODO : 검색어 증강(한국어 -> 영어, 일본어 등등 언어로 증강)이 중요한 것 같음. llm이 잘할 것 같은데.
    # TODO : DB에 계속해서 쌓아나가면 좋을 것 같다.
    for lookup_track_name, lookup_artist in lookup_candidates:
        try:
            # lastfm에서 비슷한 track search
            lf_track = lastfm.get_track(lookup_artist, lookup_track_name)
            # 캐시 키도 같은 이유로 문장부호를 보존해야 한다. compact_text를 쓰면
            # 앞선 표기의 빈 결과가 캐시되어 뒤 표기가 호출조차 되지 않는다.
            cache_artist, cache_name = _lookup_key(lookup_artist, lookup_track_name)
            raw = await sources._lf_call(
                f"lf:track_similar:{cache_artist}:{cache_name}:{limit}",
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


async def _collect_tag_tracks(
    tags: list[str],
    lastfm: pylast.LastFMNetwork,
    limit: int,
) -> list[list[TrackInfo]]:
    tags = _unique_preserve_order(tags)

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
            tag_priority = scoring._tag_weight(tag_index)
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


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result
