import logging

from fastapi import APIRouter, Request

from app.schemas.recommend import RecommendRequest, RecommendResponse
from app.services.recommend_service import run_recommend

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest, request: Request):
    """
    유저의 query를 입력받아 추천결과를 return
    """
    http = request.app.state.http
    lastfm = request.app.state.lastfm_pylast

    logger.info("recommend request - query=%r, top_n=%d", req.query, req.top_n)
    result = await run_recommend(req.query, req.top_n, http, lastfm)
    return RecommendResponse(**result)
