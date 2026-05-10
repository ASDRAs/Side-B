import asyncio
import logging
from dataclasses import asdict

import pylast
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from recommend_algo import (
    hidden_discovery,
    normalize_input,
    opposite_emotion,
    resolve_album_art,
    reverse_top100,
    similar_listening_pattern,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class RecommendRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    top_n: int = Field(default=10, ge=1, le=50)


class RecommendResponse(BaseModel):
    track_name: str
    artist: str
    top_n: int
    result: dict
    source_id: str | None = None
    album_art_url: str | None = None


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest, request: Request):
    http = request.app.state.http
    lastfm = getattr(request.app.state, "lastfm", None)
    if lastfm is None:
        settings = request.app.state.settings
        lastfm = pylast.LastFMNetwork(
            api_key=settings.lastfm_api_key,
            api_secret=settings.lastfm_api_secret,
        )
        request.app.state.lastfm = lastfm

    logger.info("recommend request - query=%r, top_n=%d", req.query, req.top_n)

    track_name, artist, source_id = await normalize_input(req.query, http, lastfm)
    if not track_name or not artist:
        return RecommendResponse(
            track_name=req.query,
            artist="Unknown",
            top_n=req.top_n,
            result={"similar": [], "reverse": [], "opposite": [], "hidden": []},
        )

    art_source_id, album_art_url = await resolve_album_art(http, track_name, artist)
    source_id = source_id or art_source_id

    raw_results = await asyncio.gather(
        similar_listening_pattern(track_name, artist, http, lastfm, top_n=req.top_n),
        reverse_top100(track_name, artist, http, lastfm, top_n=req.top_n),
        opposite_emotion(track_name, artist, http, lastfm, top_n=req.top_n),
        hidden_discovery(track_name, artist, http, lastfm, top_n=req.top_n),
        return_exceptions=True,
    )

    processed_results = []
    for result in raw_results:
        if isinstance(result, Exception):
            logger.error("recommendation algorithm error: %s", result, exc_info=True)
            processed_results.append([])
        else:
            processed_results.append([asdict(track) for track in result])

    return RecommendResponse(
        track_name=track_name,
        artist=artist,
        top_n=req.top_n,
        source_id=source_id,
        album_art_url=album_art_url,
        result={
            "similar": processed_results[0],
            "reverse": processed_results[1],
            "opposite": processed_results[2],
            "hidden": processed_results[3],
        },
    )
