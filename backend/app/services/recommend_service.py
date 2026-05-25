import asyncio
import logging
from dataclasses import asdict

import httpx
import pylast

from recommend_algo import (
    _track_similar_tracks,
    hidden_discovery,
    normalize_input,
    opposite_emotion,
    resolve_album_art,
    reverse_top100,
    similar_listening_pattern,
    tag_based_recommendations,
)

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
    name, artist, source_id = await normalize_input(query, http, lastfm)

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

    art_source_id, album_art_url = await resolve_album_art(http, name, artist)
    source_id = source_id or art_source_id

    try:
        _prefetch_limit = max(60, top_n * 6)
        prefetched_similar = await _track_similar_tracks(
            name, artist, lastfm, _prefetch_limit
        )
    except Exception as exc:
        logger.warning(
            "[Prefetch] get_similar failed, running algorithms independently: %s", exc
        )
        prefetched_similar = None

    raw_results = await asyncio.gather(
        similar_listening_pattern(
            name, artist, http, lastfm, top_n=top_n, prefetched=prefetched_similar
        ),
        reverse_top100(
            name, artist, http, lastfm, top_n=top_n, prefetched=prefetched_similar
        ),
        opposite_emotion(name, artist, http, lastfm, top_n=top_n),
        hidden_discovery(name, artist, http, lastfm, top_n=top_n),
        return_exceptions=True,
    )

    processed_results = []
    for result in raw_results:
        if isinstance(result, Exception):
            logger.error("recommendation algorithm error: %s", result, exc_info=True)
            processed_results.append([])
        else:
            processed_results.append([asdict(track) for track in result])

    return dict(
        track_name=name,
        artist=artist,
        top_n=top_n,
        source_id=source_id,
        album_art_url=album_art_url,
        result={
            "similar": processed_results[0],
            "reverse": processed_results[1],
            "opposite": processed_results[2],
            "hidden": processed_results[3],
        },
    )
