"""
main.py
FastAPI entrypoint for the iTunes + Deezer + Last.fm discovery API.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
import pylast
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from recommend_algo import (
    hidden_discovery,
    normalize_input,
    opposite_emotion,
    resolve_album_art,
    reverse_top100,
    similar_listening_pattern,
    tag_based_recommendations,
)
from preview import router as preview_router

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class RecommendRequest(BaseModel):
    query: str
    top_n: int = 10


class RecommendResponse(BaseModel):
    track_name: str
    artist: str
    top_n: int
    result: dict
    source_id: str | None = None
    album_art_url: str | None = None


def _pick_representative_track(tag_results: dict):
    """Choose the track that should appear in the result screen seed label."""
    return next(
        (track for tracks in tag_results.values() for track in tracks if track.album_art_url),
        None,
    ) or next((track for tracks in tag_results.values() for track in tracks), None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    lastfm_key = os.getenv("LASTFM_API_KEY")
    if not lastfm_key:
        logger.warning("LASTFM_API_KEY가 .env에 없습니다. Last.fm 로직이 실패할 수 있습니다.")

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )

    app.state.http = http_client
    app.state.lastfm = pylast.LastFMNetwork(api_key=lastfm_key)

    logger.info("Lifespan: API Clients initialized (iTunes/Deezer/Last.fm Mode)")
    try:
        yield
    finally:
        await http_client.aclose()


app = FastAPI(title="Music Discovery API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(preview_router)


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "itunes_deezer_lastfm"}


@app.get("/api/health")
async def api_health():
    return await health()


@app.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest):
    logger.info("Request: %r", req.query)

    http = app.state.http
    lf = app.state.lastfm

    name, artist, source_id = await normalize_input(req.query, http, lf)

    if not name or not artist:
        tag_results = await tag_based_recommendations(req.query, http, lf, top_n=req.top_n)
        if tag_results and any(tag_results.values()):
            processed_tag_results = {
                key: [asdict(track) for track in tracks]
                for key, tracks in tag_results.items()
            }
            representative = _pick_representative_track(tag_results)
            logger.info("Tag fallback used for query: %s", req.query)
            return RecommendResponse(
                track_name=representative.name if representative else req.query,
                artist=representative.artist if representative else "태그 기반 추천",
                top_n=req.top_n,
                source_id=representative.source_id if representative else None,
                album_art_url=representative.album_art_url if representative else None,
                result=processed_tag_results,
            )

        logger.warning("No results found for query: %s", req.query)
        return RecommendResponse(
            track_name=req.query,
            artist="Unknown",
            top_n=req.top_n,
            result={"similar": [], "reverse": [], "opposite": [], "hidden": []},
        )

    art_source_id, album_art_url = await resolve_album_art(http, name, artist)
    source_id = source_id or art_source_id

    raw_results = await asyncio.gather(
        similar_listening_pattern(name, artist, http, lf, top_n=req.top_n),
        reverse_top100(name, artist, http, lf, top_n=req.top_n),
        opposite_emotion(name, artist, http, lf, top_n=req.top_n),
        hidden_discovery(name, artist, http, lf, top_n=req.top_n),
        return_exceptions=True,
    )

    processed_results = []
    for result in raw_results:
        if isinstance(result, Exception):
            logger.error("Algorithm Failure: %s", result, exc_info=True)
            processed_results.append([])
        else:
            processed_results.append([asdict(track) for track in result])

    return RecommendResponse(
        track_name=name,
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
