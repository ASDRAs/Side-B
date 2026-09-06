from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.routers.genre_classification import router
from app.services.access import BackendAccess
from app.services.inference_client import InferenceClient
from app.utils.preview_audio import PreviewBytes, PreviewNotFoundError


@pytest.mark.parametrize("status,expected", [(200, 200), (422, 422), (503, 503)])
async def test_route_resolves_audio_then_calls_private_service(
    monkeypatch, status, expected
):
    async def preview(**kwargs):
        assert kwargs["track_name"] == "Input title"
        return PreviewBytes(
            b"resolved-audio", "itunes", "Resolved title", "Resolved artist"
        )

    monkeypatch.setattr(
        "app.services.genre_classification_service.load_track_preview_bytes", preview
    )

    def infer(request):
        assert request.content == b"resolved-audio"
        return httpx.Response(
            status, json={"genre": "pop", "score": -0.4, "model_version": "v1"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(infer)) as http:
        app = make_app(http)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as caller:
            response = await caller.post(
                "/genre-classification",
                json={"track_name": "Input title", "artist": "Input artist"},
                headers={"X-Side-B-Access-Token": "team-token"},
            )
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {
            "track_name": "Resolved title",
            "artist": "Resolved artist",
            "genre": "pop",
            "score": -0.4,
            "model_version": "v1",
        }


def make_app(http, url="http://localhost:8082"):
    app = FastAPI()
    app.include_router(router)
    app.state.http = http
    app.state.settings = SimpleNamespace(
        gemini_api_key="test-key", gemini_model="gemini-2.5-flash"
    )
    app.state.genre_inference = InferenceClient(http, url, use_iam=False)
    app.state.recommend_access = BackendAccess("team-token")
    return app


@pytest.mark.parametrize(
    "token,url,expected", [(None, "http://localhost", 401), ("team-token", "", 503)]
)
async def test_route_configuration_and_auth_fail_before_network(token, url, expected):
    def unexpected(request):
        pytest.fail("Network should not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        app = make_app(http, url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as caller:
            headers = {"X-Side-B-Access-Token": token} if token else {}
            result = await caller.post(
                "/genre-classification",
                json={"track_name": "Track", "artist": "Artist"},
                headers=headers,
            )
    assert result.status_code == expected


async def test_preview_miss_is_not_an_inference_failure(monkeypatch):
    async def miss(**kwargs):
        raise PreviewNotFoundError("not found")

    monkeypatch.setattr(
        "app.services.genre_classification_service.load_track_preview_bytes", miss
    )
    async with httpx.AsyncClient() as http:
        app = make_app(http)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as caller:
            result = await caller.post(
                "/genre-classification",
                json={"track_name": "Track", "artist": "Artist"},
                headers={"X-Side-B-Access-Token": "team-token"},
            )
    assert result.status_code == 404
    assert result.json()["detail"]["code"] == "preview_not_found"
