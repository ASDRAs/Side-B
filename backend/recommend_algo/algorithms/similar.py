import logging

import httpx
import pylast

from recommend_algo.common import scoring, seeds, sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)


async def similar_listening_pattern(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n=10,
    *,
    prefetched=None,
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

        # 중복 제거
        raw_tracks = [
            TrackInfo(
                name=item.item.get_name(),
                artist=item.item.get_artist().get_name(),
                match_score=float(item.match),
            )
            for item in raw_similar
        ]
        unique_tracks = scoring._dedupe_tracks(raw_tracks)

        # score 기준으로 정렬
        sorted_tracks = sorted(
            unique_tracks, key=lambda t: t.match_score or 0, reverse=True
        )
        top_tracks = sorted_tracks[:top_n]
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
