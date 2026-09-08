"""
FastAPI entrypoint — recommendation pipeline (iTunes + Deezer + Last.fm + Gemini).
"""

import logging
from contextlib import asynccontextmanager

import httpx
import pylast
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers.genre_classification import (
    router as genre_classification_router,
)
from app.routers.recommend import router as recommend_router
from app.routers.youtube_export import router as youtube_export_router
from app.services.access import BackendAccess
from app.services.inference_client import InferenceClient, InferenceConfigurationError
from app.services.youtube import YouTubeMatcher, YouTubeSearchClient
from preview import router as preview_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# httpx의 INFO 로그에는 외부 API URL과 쿼리 파라미터가 포함될 수 있다.
# 운영 로그에 API 키가 노출되지 않도록 요청 URL 로깅을 비활성화한다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )

    app.state.settings = settings
    app.state.http = http
    app.state.youtube_matcher = YouTubeMatcher(
        YouTubeSearchClient(
            http,
            settings.youtube_api_key,
            max_results=settings.youtube_search_max_results,
            daily_budget=settings.youtube_search_daily_budget,
        ),
        threshold=settings.youtube_match_threshold,
        concurrency=settings.youtube_search_concurrency,
    )
    app.state.youtube_export_access = BackendAccess(
        settings.backend_access_token,
        requests_per_minute=settings.youtube_export_requests_per_minute,
    )
    unauthenticated = (
        settings.allow_unauthenticated_recommend and not settings.backend_access_token
    )
    app.state.recommend_access = (
        None
        if unauthenticated
        else BackendAccess(
            settings.backend_access_token,
            requests_per_minute=settings.recommend_requests_per_minute,
        )
    )
    # 자동 EQ는 곡이 바뀔 때마다 한 번씩 호출한다. 버킷을 분리하지 않으면 곡을
    # 여러 번 넘기는 것만으로 /recommend가 429로 막힌다.
    app.state.genre_access = (
        None
        if unauthenticated
        else BackendAccess(
            settings.backend_access_token,
            requests_per_minute=settings.genre_requests_per_minute,
        )
    )
    app.state.lastfm_pylast = pylast.LastFMNetwork(
        api_key=settings.lastfm_api_key or "",
        api_secret=settings.lastfm_api_secret or "",
    )

    # 잘못된 URL로 부팅 전체를 막지 않는다. 장르 분류만 비활성화하고 추천은 살린다.
    try:
        app.state.genre_inference = InferenceClient(
            http,
            settings.clap_inference_url,
            audience=settings.clap_inference_audience,
            use_iam=settings.clap_inference_use_iam,
            timeout=settings.clap_inference_timeout_seconds,
        )
    except InferenceConfigurationError:
        logger.exception(
            "CLAP_INFERENCE_URL is invalid — genre classification disabled."
        )
        app.state.genre_inference = None

    if not settings.lastfm_api_key:
        logger.warning("LASTFM_API_KEY not set — Last.fm calls will fail.")
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — LLM scoring disabled.")
    if not settings.youtube_api_key:
        logger.warning("YOUTUBE_API_KEY not set — YouTube playlist matching disabled.")
    if settings.allow_unauthenticated_recommend and not settings.backend_access_token:
        logger.warning(
            "ALLOW_UNAUTHENTICATED_RECOMMEND enabled — use local development only."
        )
    if not settings.backend_access_token:
        logger.warning(
            "SIDE_B_ACCESS_TOKEN not set — protected endpoints are disabled."
        )

    logger.info("Startup complete (model=%s)", settings.gemini_model)
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(title="Music Discovery API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_allowlist,
    allow_methods=["GET", "POST"],
    allow_headers=[
        "Content-Type",
        "X-Side-B-Access-Token",
        "X-Side-B-Export-Token",
    ],
)

# router 설정 fetch하면 아래의 기능들 불러옴. 실제 기능들이 수행되는 곳
app.include_router(preview_router)
app.include_router(recommend_router)
app.include_router(youtube_export_router)
app.include_router(genre_classification_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/health")
async def api_health():
    return await health()
