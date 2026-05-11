import logging

import pylast
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services.recommend_service import run_recommend

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
    lastfm = request.app.state.lastfm_pylast

    logger.info("recommend request - query=%r, top_n=%d", req.query, req.top_n)
    result = await run_recommend(req.query, req.top_n, http, lastfm)
    return RecommendResponse(**result)
