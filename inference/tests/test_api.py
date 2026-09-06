import asyncio
import threading

import httpx
import pytest

from inference.main import InvalidAudio, MAX_BYTES, Prediction, create_app


class Predictor:
    model_version = "fixture-v1"

    def __call__(self, audio):
        if audio == b"invalid":
            raise InvalidAudio("Invalid fixture")
        return Prediction(genre="pop", score=-0.1, model_version=self.model_version)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,content_type,status",
    [
        (b"audio", "audio/mpeg", 200),
        (b"", "audio/mpeg", 422),
        (b"invalid", "audio/mpeg", 422),
        (b"{}", "application/json", 415),
        (b"a" * (MAX_BYTES + 1), "audio/mpeg", 413),
    ],
    ids=["valid", "empty", "invalid", "json", "oversized"],
)
async def test_audio_contract(body, content_type, status):
    app = create_app(Predictor)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/predict", content=body, headers={"Content-Type": content_type}
            )
            assert response.status_code == status
            assert (await client.get("/health")).json()["model_version"] == "fixture-v1"
            assert not app.state.lock.locked()


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_allow_parallel_native_inference():
    started = threading.Event()
    finish = threading.Event()

    class BlockingPredictor(Predictor):
        def __call__(self, audio):
            started.set()
            assert finish.wait(5)
            return super().__call__(audio)

    app = create_app(BlockingPredictor)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(
                client.post(
                    "/predict", content=b"audio", headers={"Content-Type": "audio/mpeg"}
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                busy = await client.post(
                    "/predict", content=b"audio", headers={"Content-Type": "audio/mpeg"}
                )
                assert busy.status_code == 503
                assert busy.headers["Retry-After"] == "1"
                assert app.state.lock.locked()
            finally:
                finish.set()
                await asyncio.gather(*app.state.tasks)
            assert not app.state.lock.locked()


@pytest.mark.asyncio
async def test_chunked_upload_cannot_bypass_size_limit():
    async def chunks():
        yield b"a" * MAX_BYTES
        yield b"b"

    app = create_app(Predictor)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/predict", content=chunks(), headers={"Content-Type": "audio/mpeg"}
            )
    assert response.status_code == 413
    assert not app.state.lock.locked()
