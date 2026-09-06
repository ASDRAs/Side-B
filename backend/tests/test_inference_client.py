import asyncio

import httpx
import pytest

from app.services.inference_client import (
    InferenceAudioError,
    InferenceClient,
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)

RESULT = {"genre": "pop", "score": -0.2, "model_version": "fixture-v1"}


async def test_tagged_request_uses_service_origin_as_identity_audience():
    def handle(request):
        if request.url.host == "metadata.google.internal":
            assert request.url.params["audience"] == "https://inference.run.app"
            return httpx.Response(200, text="fixture-token")
        assert str(request.url) == "https://candidate---inference.run.app/predict"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        return httpx.Response(200, json=RESULT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = InferenceClient(
            http,
            "https://candidate---inference.run.app",
            audience="https://inference.run.app",
        )
        assert (await client.predict(b"audio")).genre == "pop"


@pytest.mark.parametrize(
    "audience",
    [
        "not-a-url",
        "http://remote.example",
        "https://user:pass@remote.example",
        "https://remote.example/path",
    ],
)
async def test_invalid_identity_audience_is_rejected(audience):
    async with httpx.AsyncClient() as http:
        with pytest.raises(InferenceConfigurationError):
            InferenceClient(http, "https://inference.run.app", audience=audience)


async def test_identity_audience_cached_and_audio_forwarded():
    calls = []

    async def handle(request):
        calls.append(request)
        if request.url.host == "metadata.google.internal":
            assert request.url.params["audience"] == "https://inference.run.app"
            assert request.headers["Metadata-Flavor"] == "Google"
            await asyncio.sleep(0)
            return httpx.Response(200, text="private-identity-token")
        assert request.url.path == "/predict"
        assert request.headers["Authorization"] == "Bearer private-identity-token"
        assert request.headers["Content-Type"] == "application/octet-stream"
        assert request.content == b"audio-data"
        return httpx.Response(200, json=RESULT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = InferenceClient(http, "https://inference.run.app")
        values = await asyncio.gather(
            *(client.predict(b"audio-data") for _ in range(3))
        )
    assert len(calls) == 4
    assert all(value.score == -0.2 for value in values)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, InferenceUnavailableError),
        (403, InferenceUnavailableError),
        (422, InferenceAudioError),
        (413, InferenceAudioError),
        (503, InferenceUnavailableError),
    ],
)
async def test_private_errors_are_mapped(status, error):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status))
    ) as http:
        client = InferenceClient(http, "http://localhost:8082", use_iam=False)
        with pytest.raises(error):
            await client.predict(b"audio")


@pytest.mark.parametrize(
    "body", [{}, {**RESULT, "score": "NaN"}, {**RESULT, "model_version": ""}]
)
async def test_invalid_prediction_rejected(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(InferenceUnavailableError):
            await InferenceClient(http, "http://localhost", use_iam=False).predict(
                b"audio"
            )


async def test_timeout_is_distinct_from_unavailable():
    def handle(request):
        raise httpx.ReadTimeout("late", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(InferenceTimeoutError):
            await InferenceClient(http, "http://localhost", use_iam=False).predict(
                b"audio"
            )


@pytest.mark.parametrize(
    "url,iam",
    [
        ("http://remote.example", True),
        ("https://remote.example", False),
        ("https://user:pass@remote.example", True),
        ("https://remote.example/path", True),
    ],
)
async def test_remote_authentication_cannot_be_disabled(url, iam):
    async with httpx.AsyncClient() as http:
        with pytest.raises(InferenceConfigurationError):
            InferenceClient(http, url, use_iam=iam)


async def test_rejected_identity_is_refreshed_next_request():
    tokens = []
    attempts = []

    def handle(request):
        if request.url.host == "metadata.google.internal":
            tokens.append(1)
            return httpx.Response(200, text=f"token-{len(tokens)}")
        attempts.append(request.headers["Authorization"])
        return (
            httpx.Response(403)
            if len(attempts) == 1
            else httpx.Response(200, json=RESULT)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = InferenceClient(http, "https://inference.run.app")
        with pytest.raises(InferenceUnavailableError):
            await client.predict(b"audio")
        await client.predict(b"audio")
    assert attempts == ["Bearer token-1", "Bearer token-2"]
