import asyncio
import logging
from dataclasses import asdict

import httpx
import pylast

from app.llm.llm_wrapper import GeminiWrapper
from recommend_algo import (
    TrackInfo,
    _track_similar_tracks,
    get_tracks_metadata,
    hidden_discovery_by_artist,
    opposite_emotion,
    preprocess_input,
    reverse_top100,
    similar_listening_pattern,
    tag_based_recommendations,
)
from recommend_algo.common.sources import analyze_music_query

logger = logging.getLogger(__name__)


def _pick_representative_track(tag_results: dict):
    return next(
        (
            track
            for tracks in tag_results.values()
            for track in tracks
            if track.album_art_url
        ),
        None,
    ) or next((track for tracks in tag_results.values() for track in tracks), None)


async def run_recommend(
    query: str,
    top_n: int,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
) -> dict:
    """
    유저의 free-form query를 받아 노래를 추천합니다.
    """

    gemini_wrapper = GeminiWrapper()
    query_analysis = analyze_music_query(query, gemini_wrapper)
    name, artist, source_id = await preprocess_input(query, http, lastfm)
    user_track_info = TrackInfo(name=name, artist=artist, source_id=source_id)
    # 유저 query에 name, artist가 없는 경우(mood tag query)
    if not name or not artist:
        tag_results = await tag_based_recommendations(query, http, lastfm, top_n=top_n)
        if tag_results and any(tag_results.values()):
            processed = {k: [asdict(t) for t in v] for k, v in tag_results.items()}
            representative = _pick_representative_track(tag_results)
            logger.info("Tag fallback used for query: %s", query)
            return dict(
                track_name=representative.name if representative else query,
                artist=representative.artist if representative else "태그 기반 추천",
                top_n=top_n,
                source_id=representative.source_id if representative else None,
                album_art_url=representative.album_art_url if representative else None,
                result=processed,
            )
        logger.warning("No results found for query: %s", query)
        return dict(
            track_name=query,
            artist="Unknown",
            top_n=top_n,
            source_id=None,
            album_art_url=None,
            result={"similar": [], "reverse": [], "opposite": [], "hidden": []},
        )

    # 유저 query에 name, artist가 있는 경우(direct query)
    else:
        user_track_info = await get_tracks_metadata(
            http, [user_track_info], lastfm, fields=["album_art", "source_id"]
        )
        user_track_info: TrackInfo = user_track_info[0]

        try:
            prefetch_limit = max(60, top_n * 6)
            # 유저가 direct로 입력한 노래와 비슷한 노래 search
            prefetched_similar = await _track_similar_tracks(
                name, artist, lastfm, prefetch_limit
            )
        except Exception as exc:
            logger.warning(
                "[Prefetch] get_similar failed, running algorithms independently: %s",
                exc,
            )
            prefetched_similar = None

        # similar, reverse, oppsite, hidden 취향의 곡들 추천
        raw_results = await asyncio.gather(
            similar_listening_pattern(
                name, artist, http, lastfm, top_n=top_n, prefetched=prefetched_similar
            ),
            reverse_top100(
                name, artist, http, lastfm, top_n=top_n, prefetched=prefetched_similar
            ),
            opposite_emotion(name, artist, http, lastfm, top_n=top_n),
            hidden_discovery_by_artist(artist, http, lastfm, top_n=top_n),
            return_exceptions=True,
        )

        rcmd_results = {
            "similar": raw_results[0],
            "reverse": raw_results[1],
            "opposite": raw_results[2],
            "hidden": raw_results[3],
        }

        processed_rcmd_results = {}
        for rcmd_type, rcmd_result in rcmd_results.items():
            if isinstance(rcmd_result, Exception):
                logger.error(
                    "recommendation algorithm error: %s", rcmd_result, exc_info=True
                )
                processed_rcmd_results[rcmd_type] = []
            else:
                processed_rcmd_results[rcmd_type] = [
                    asdict(track) for track in rcmd_result
                ]

        return dict(
            track_name=user_track_info.name,
            artist=user_track_info.artist,
            top_n=top_n,
            source_id=user_track_info.source_id,
            album_art_url=user_track_info.album_art_url,
            result={
                "similar": processed_rcmd_results["similar"],
                "reverse": processed_rcmd_results["reverse"],
                "opposite": processed_rcmd_results["opposite"],
                "hidden": processed_rcmd_results["hidden"],
            },
        )
