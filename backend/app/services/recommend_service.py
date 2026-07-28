import logging
from collections.abc import Awaitable
from dataclasses import asdict

import httpx
import pylast

from app.config import Settings
from app.llm.llm_wrapper import GeminiWrapper
from recommend_algo import (
    TrackInfo,
    _track_similar_tracks,
    get_tracks_metadata,
    hidden_discovery_by_artist,
    preprocess_input,
    reverse_top100,
    similar_listening_pattern,
    tag_based_recommendations,
)
from recommend_algo.common import scoring
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


def _accept_unseen_tracks(
    tracks: list[TrackInfo],
    seen_keys: set[str],
) -> list[TrackInfo]:
    accepted: list[TrackInfo] = []
    for track in tracks:
        key = scoring._track_key(track)
        if not key.strip(":") or key in seen_keys:
            continue
        seen_keys.add(key)
        accepted.append(track)
    return accepted


def _empty_recommendation(query: str, top_n: int, buckets: tuple[str, ...]) -> dict:
    """추천 결과를 만들지 못했을 때 돌려줄 빈 응답."""
    return dict(
        track_name=query,
        artist="Unknown",
        top_n=top_n,
        source_id=None,
        album_art_url=None,
        result={bucket: [] for bucket in buckets},
    )


async def _run_recommendation_bucket(
    bucket: str,
    operation: Awaitable[list[TrackInfo]],
) -> list[TrackInfo]:
    try:
        return await operation
    except Exception:
        logger.exception("recommendation bucket failed: %s", bucket)
        return []


async def _run_direct_recommendations(
    name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n: int,
    prefetched_similar,
) -> dict[str, list[TrackInfo]]:
    """Run buckets in display order so later buckets can backfill around duplicates."""
    seen_keys: set[str] = set()
    results: dict[str, list[TrackInfo]] = {}

    similar = await _run_recommendation_bucket(
        "similar",
        similar_listening_pattern(
            name,
            artist,
            http,
            lastfm,
            top_n=top_n,
            prefetched=prefetched_similar,
            excluded_keys=seen_keys,
        ),
    )
    results["similar"] = _accept_unseen_tracks(similar, seen_keys)

    reverse = await _run_recommendation_bucket(
        "reverse",
        reverse_top100(
            name,
            artist,
            http,
            lastfm,
            top_n=top_n,
            prefetched=prefetched_similar,
            excluded_keys=seen_keys,
        ),
    )
    results["reverse"] = _accept_unseen_tracks(reverse, seen_keys)

    hidden = await _run_recommendation_bucket(
        "hidden",
        hidden_discovery_by_artist(
            artist,
            http,
            lastfm,
            top_n=top_n,
            excluded_keys=seen_keys,
        ),
    )
    results["hidden"] = _accept_unseen_tracks(hidden, seen_keys)

    return results


async def run_recommend(
    query: str,
    top_n: int,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    settings: Settings,
) -> dict:
    """
    유저의 free-form query를 받아 노래를 추천합니다.
    """

    gemini_wrapper = GeminiWrapper(settings.gemini_api_key, settings.gemini_model)
    query_analysis = analyze_music_query(query, gemini_wrapper)
    user_intent = query_analysis.intent

    if user_intent == "meaningless":
        logger.warning("Query is meaningless: %s", query)
        return _empty_recommendation(query, top_n, ("similar", "reverse", "hidden"))

    # 유저 query에 name, artist가 없는 경우(mood tag query)
    elif user_intent == "mood":
        seed_tags = query_analysis.mood.tags
        opposite_tags = query_analysis.mood.opposite_tags
        tag_results = await tag_based_recommendations(
            http,
            lastfm,
            seed_tags,
            opposite_tags,
            top_n=top_n,
        )
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
        return _empty_recommendation(query, top_n, ("similar", "opposite", "hidden"))

    # 유저 query에 name, artist가 있는 경우(direct query)
    else:
        user_music_query = query_analysis.direct.search_query
        alternative_queries = query_analysis.direct.alternative_queries
        name, artist, source_id = await preprocess_input(
            user_music_query, alternative_queries, http, lastfm
        )
        # iTunes/Last.fm 모두 곡을 특정하지 못하면 (None, None, None)이 돌아온다.
        # 그대로 진행하면 메타데이터 조회에서 TypeError가 나므로 여기서 끊는다.
        if not name or not artist:
            logger.warning("Direct query could not be resolved: %s", query)
            return _empty_recommendation(query, top_n, ("similar", "reverse", "hidden"))

        user_track_info = TrackInfo(name=name, artist=artist, source_id=source_id)

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

        # direct 검색은 similar, reverse, hidden 세 방향으로 추천
        rcmd_results = await _run_direct_recommendations(
            name,
            artist,
            http,
            lastfm,
            top_n,
            prefetched_similar,
        )

        processed_rcmd_results = {
            rcmd_type: [asdict(track) for track in rcmd_result]
            for rcmd_type, rcmd_result in rcmd_results.items()
        }

        return dict(
            track_name=user_track_info.name,
            artist=user_track_info.artist,
            top_n=top_n,
            source_id=user_track_info.source_id,
            album_art_url=user_track_info.album_art_url,
            result={
                "similar": processed_rcmd_results["similar"],
                "reverse": processed_rcmd_results["reverse"],
                "hidden": processed_rcmd_results["hidden"],
            },
        )
