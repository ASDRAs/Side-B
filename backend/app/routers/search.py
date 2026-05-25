import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.pipeline.errors import (
    NoResultsError,
    SearchTimeoutError,
    ServiceUnavailableError,
)
from app.pipeline.orchestrator import search_pipeline
from app.schemas.search import SearchRequest, Track

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/search", response_model=Track)
async def search(request_body: SearchRequest, request: Request):
    settings = request.app.state.settings
    try:
        return await search_pipeline(
            query=request_body.query,
            catalog=request.app.state.catalog,
            lastfm=request.app.state.lastfm,
            llm=request.app.state.llm,
            timeout_seconds=settings.search_timeout_seconds,
            use_deterministic=request_body.useDeterministic,
            use_llm=request_body.useLlm,
        )
    except NoResultsError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "no_results", "query": exc.query},
        )
    except SearchTimeoutError as exc:
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={"error": "timeout", "query": exc.query},
        )
    except ServiceUnavailableError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "service_unavailable", "detail": exc.detail},
        )
    except Exception:
        logger.exception("Unexpected search failure.")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )
