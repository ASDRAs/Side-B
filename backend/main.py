"""
FastAPI entrypoint — search pipeline (iTunes + Deezer + Last.fm + Gemini).
"""

import logging
from contextlib import asynccontextmanager

import httpx
import pylast
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers.recommend import router as recommend_router
from app.routers.search import router as search_router
from app.services.catalog import CatalogClient
from app.services.lastfm import LastFmClient
from app.services.llm import GeminiClient
from preview import router as preview_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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
    app.state.catalog = CatalogClient(http)
    app.state.lastfm = LastFmClient(settings.lastfm_api_key, http)
    app.state.llm = GeminiClient(settings.gemini_api_key, settings.gemini_model, http)
    app.state.lastfm_pylast = pylast.LastFMNetwork(
        api_key=settings.lastfm_api_key or "",
        api_secret=settings.lastfm_api_secret or "",
    )

    if not settings.lastfm_api_key:
        logger.warning("LASTFM_API_KEY not set — Last.fm calls will fail.")
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — LLM scoring disabled.")

    logger.info("Startup complete (model=%s)", settings.gemini_model)
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(title="Music Discovery API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(preview_router)
app.include_router(recommend_router)
app.include_router(search_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/health")
async def api_health():
    return await health()
