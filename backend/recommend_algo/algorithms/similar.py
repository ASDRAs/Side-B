import asyncio
import logging

import httpx
import pylast

from app.utils.text import compact_text
from recommend_algo.common import lastfm_raw, scoring, seeds, sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)


async def _similar_artist_fallback(
    artist: str,
    lastfm: pylast.LastFMNetwork,
    top_n: int,
) -> list[TrackInfo]:
    """Track similarity가 비어 있을 때 유사 아티스트의 대표곡으로 백필한다."""
    artist_limit = max(3, min(top_n * 2, 8))
    track_limit = max(3, top_n * 2)
    seed_artist = lastfm.get_artist(artist)
    similar_artists = await sources._lf_call(
        f"lf:artist_similar:{compact_text(artist)}:{artist_limit}",
        600,
        seed_artist.get_similar,
        artist_limit,
    )

    async def _fetch(artist_result) -> list[TrackInfo]:
        similar_artist = artist_result.item
        similar_artist_name = str(similar_artist.get_name() or "").strip()
        if not similar_artist_name:
            return []
        artist_match = float(getattr(artist_result, "match", 0) or 0)
        top_tracks = await sources._lf_call(
            f"lf:artist_top:{compact_text(similar_artist_name)}:{track_limit}",
            600,
            lastfm_raw.artist_top_tracks,
            similar_artist,
            track_limit,
        )
        return [
            TrackInfo(
                name=str(item.item.get_name() or "").strip(),
                artist=similar_artist_name,
                match_score=max(
                    0.0,
                    artist_match - (rank / max(track_limit, 1)) * 0.1,
                ),
                reason_tags=[similar_artist_name],
                signals=lastfm_raw.signals_of(item),
            )
            for rank, item in enumerate(top_tracks or [])
            if str(item.item.get_name() or "").strip()
        ]

    fetched = await asyncio.gather(
        *[_fetch(item) for item in similar_artists or []],
        return_exceptions=True,
    )
    candidates = [
        track
        for result in fetched
        if not isinstance(result, Exception)
        for track in result
    ]
    ranked = sorted(
        scoring._dedupe_tracks(candidates),
        key=lambda track: track.match_score or 0,
        reverse=True,
    )
    diverse = scoring._cap_per_artist(ranked, max_per=1)
    return scoring._fill_from_ranked_pool(diverse, ranked, top_n)


async def similar_listening_pattern(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n=10,
    *,
    prefetched=None,
    excluded_keys: set[str] | None = None,
) -> list[TrackInfo]:
    """
    유저가 검색한 track과 유사한 tack을 추천하는 함수
    lastfm의 match score를 기준으로 정렬하여 추천합니다.
    """
    try:
        if prefetched is not None:
            raw_similar = prefetched
        else:
            # NOTE : 이거 하는 이유가? error 발생해서 None이 넘어올건데 2중으로 call하는 이유는 없어보임
            raw_similar = await seeds._track_similar_tracks(
                track_name, artist, lastfm, 50
            )

        if raw_similar:
            raw_tracks = [
                TrackInfo(
                    name=item.item.get_name(),
                    artist=item.item.get_artist().get_name(),
                    match_score=float(item.match),
                    signals=lastfm_raw.signals_of(item),
                )
                for item in raw_similar
            ]
        else:
            logger.info(
                "[similar_listening_pattern] track similarity empty; "
                "using similar artists"
            )
            raw_tracks = await _similar_artist_fallback(artist, lastfm, top_n)

        # 중복 제거
        unique_tracks = scoring._dedupe_tracks(raw_tracks)

        # score 기준으로 정렬
        sorted_tracks = sorted(
            unique_tracks, key=lambda t: t.match_score or 0, reverse=True
        )
        excluded = excluded_keys or set()
        top_tracks = [
            track
            for track in sorted_tracks
            if scoring._track_key(track) not in excluded
        ][:top_n]
        top_tracks = await sources.get_tracks_metadata(http, top_tracks, lastfm)
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
