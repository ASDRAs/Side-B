"""Private HTTP entrypoint. Cloud Run IAM authenticates service callers."""

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

MAX_BYTES = 10 * 1024 * 1024
logger = logging.getLogger(__name__)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genre: str = Field(min_length=1)
    score: float = Field(allow_inf_nan=False)
    model_version: str = Field(min_length=1)


class InvalidAudio(ValueError):
    pass


def load_predictor():
    from inference.pipeline import Predictor

    return Predictor()


def create_app(loader: Callable = load_predictor) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.predictor = await asyncio.to_thread(loader)
        app.state.lock = asyncio.Lock()
        app.state.tasks = set()
        yield
        if app.state.tasks:
            await asyncio.gather(*app.state.tasks, return_exceptions=True)

    app = FastAPI(
        title="Side-B audio inference", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.get("/health")
    async def health():
        if not hasattr(app.state, "predictor"):
            raise HTTPException(503, "Models are not ready")
        return {"status": "ready", "model_version": app.state.predictor.model_version}

    @app.post("/predict", response_model=Prediction)
    async def predict(request: Request):
        content_type = request.headers.get("content-type", "").split(";", 1)[0]
        if not (
            content_type.startswith("audio/")
            or content_type == "application/octet-stream"
        ):
            raise HTTPException(415, "Send audio bytes, not a URL or JSON")
        length = request.headers.get("content-length")
        if length:
            try:
                size = int(length)
            except ValueError:
                raise HTTPException(400, "Invalid content length") from None
            if size < 0:
                raise HTTPException(400, "Invalid content length")
            if size > MAX_BYTES:
                raise HTTPException(413, "Audio exceeds 10 MiB")
        lock = app.state.lock
        if lock.locked():
            raise HTTPException(503, "Inference is busy", headers={"Retry-After": "1"})
        await lock.acquire()
        try:
            data = bytearray()
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise HTTPException(413, "Audio exceeds 10 MiB")
                    data.extend(chunk)
            if not data:
                raise HTTPException(422, "Audio is empty")
        except TimeoutError as exc:
            lock.release()
            raise HTTPException(408, "Audio upload timed out") from exc
        except BaseException:
            lock.release()
            raise

        async def run():
            try:
                return await asyncio.to_thread(app.state.predictor, bytes(data))
            except InvalidAudio as exc:
                raise HTTPException(422, str(exc)) from exc
            except Exception as exc:
                logger.error("Inference failed: %s", type(exc).__name__)
                raise HTTPException(500, "Inference failed") from exc
            finally:
                lock.release()

        # Client disconnects must not free the slot while native inference runs.
        task = asyncio.create_task(run())
        app.state.tasks.add(task)

        def completed(done):
            app.state.tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    return app


app = create_app()
