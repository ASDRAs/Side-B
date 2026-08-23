import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.llm.llm_response import DirectSearchAnalysis, MusicQueryAnalysis
from app.routers.recommend import RecommendRequest, RecommendResponse, recommend
from app.services.access import BackendAccess
from app.services.recommend_service import (
    _pick_representative_track,
    _run_direct_recommendations,
    _serialize_tracks,
    run_recommend,
)
from main import app
from recommend_algo import TrackInfo, binding_from_source_id
from recommend_algo.common import scoring


def test_public_api_surface_is_recommend_only():
    paths = {route.path for route in app.routes}

    assert "/health" in paths
    assert "/recommend" in paths
    assert "/search" not in paths


def test_http_client_request_urls_are_not_logged_at_info_level():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def _recommend_request(access):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                recommend_access=access,
                http=None,
                lastfm_pylast=None,
            )
        )
    )


async def test_recommend_requires_valid_team_token(monkeypatch):
    monkeypatch.setattr("app.routers.recommend.get_settings", lambda: SimpleNamespace())
    request = _recommend_request(BackendAccess("team-token"))

    with pytest.raises(HTTPException) as exc_info:
        await recommend(RecommendRequest(query="Radiohead Creep"), request, None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "recommend_unauthorized"


async def test_recommend_uses_separate_request_limit(monkeypatch):
    async def fake_run_recommend(*args, **kwargs):
        return {
            "track_name": "Creep",
            "artist": "Radiohead",
            "top_n": 10,
            "result": {"similar": [], "reverse": [], "hidden": []},
        }

    monkeypatch.setattr("app.routers.recommend.run_recommend", fake_run_recommend)
    monkeypatch.setattr("app.routers.recommend.get_settings", lambda: SimpleNamespace())
    request = _recommend_request(BackendAccess("team-token", requests_per_minute=1))

    await recommend(RecommendRequest(query="Radiohead Creep"), request, "team-token")
    with pytest.raises(HTTPException) as exc_info:
        await recommend(
            RecommendRequest(query="Radiohead Creep"), request, "team-token"
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


async def test_local_recommend_can_explicitly_bypass_access_gate(monkeypatch):
    async def fake_run_recommend(*args, **kwargs):
        return {
            "track_name": "Creep",
            "artist": "Radiohead",
            "top_n": 10,
            "result": {"similar": [], "reverse": [], "hidden": []},
        }

    monkeypatch.setattr("app.routers.recommend.run_recommend", fake_run_recommend)
    monkeypatch.setattr("app.routers.recommend.get_settings", lambda: SimpleNamespace())

    response = await recommend(
        RecommendRequest(query="Radiohead Creep"),
        _recommend_request(None),
        None,
    )

    assert response.track_name == "Creep"


def test_recommend_contract_uses_query_source_and_hidden_bucket():
    request = RecommendRequest(query="아이유 너랑나")
    response = RecommendResponse(
        track_name="너랑나",
        artist="IU",
        top_n=request.top_n,
        source_id="itunes:1",
        album_art_url="https://example.com/art.jpg",
        result={"similar": [], "reverse": [], "hidden": []},
    )

    assert request.query == "아이유 너랑나"
    assert request.top_n == 10
    assert response.result.hidden == []
    assert response.source_id == "itunes:1"


def test_recommend_contract_rejects_blank_recommended_track_fields():
    with pytest.raises(ValidationError):
        RecommendResponse(
            track_name="너랑나",
            artist="IU",
            top_n=10,
            result={
                "similar": [{"name": "너랑나", "artist": ""}],
                "reverse": [],
                "hidden": [],
            },
        )


def test_recommend_contract_accepts_catalog_metadata_longer_than_export_limit():
    long_name = "x" * 201

    response = RecommendResponse(
        track_name="Seed",
        artist="Artist",
        top_n=10,
        result={
            "similar": [{"name": long_name, "artist": "Artist"}],
            "reverse": [],
            "hidden": [],
        },
    )

    assert response.result.similar[0].name == long_name


def test_recommend_serializer_drops_tracks_without_export_identity():
    tracks = [
        TrackInfo(name="Valid", artist="Artist"),
        TrackInfo(name="Missing artist", artist=""),
        TrackInfo(name="", artist="Missing title"),
    ]

    serialized = _serialize_tracks(tracks)

    assert [(track["name"], track["artist"]) for track in serialized] == [
        ("Valid", "Artist")
    ]


def test_recommend_contract_rejects_non_ten_track_bucket_size():
    with pytest.raises(ValidationError):
        RecommendRequest(query="아이유 너랑나", top_n=3)


def test_tag_fallback_seed_label_uses_representative_track():
    representative = TrackInfo(
        name="Under Caffeine",
        artist="Stella Jang",
        album_art_url="https://example.com/under-caffeine.jpg",
    )
    representative.bind(binding_from_source_id("lastfm:under-caffeine"))
    tag_results = {
        "similar": [representative],
        "opposite": [],
        "hidden": [],
    }

    assert _pick_representative_track(tag_results) == representative


async def test_direct_recommendations_are_disjoint_and_backfilled(monkeypatch):
    calls = []
    candidates = {
        "similar": [
            TrackInfo(name="A", artist="Artist A"),
            TrackInfo(name="B", artist="Artist B"),
        ],
        "reverse": [
            TrackInfo(name="A", artist="Artist A"),
            TrackInfo(name="C", artist="Artist C"),
            TrackInfo(name="D", artist="Artist D"),
        ],
        "hidden": [
            TrackInfo(name="E", artist="Artist E"),
            TrackInfo(name="G", artist="Artist G"),
            TrackInfo(name="H", artist="Artist H"),
        ],
    }

    def fake_runner(bucket):
        async def _run(*args, top_n, excluded_keys, **kwargs):
            calls.append((bucket, set(excluded_keys)))
            return [
                track
                for track in candidates[bucket]
                if scoring._track_key(track) not in excluded_keys
            ][:top_n]

        return _run

    monkeypatch.setattr(
        "app.services.recommend_service.similar_listening_pattern",
        fake_runner("similar"),
    )
    monkeypatch.setattr(
        "app.services.recommend_service.reverse_top100",
        fake_runner("reverse"),
    )
    monkeypatch.setattr(
        "app.services.recommend_service.hidden_discovery_by_artist",
        fake_runner("hidden"),
    )

    result = await _run_direct_recommendations(
        "Seed",
        "Seed Artist",
        None,
        None,
        top_n=2,
        prefetched_similar=[],
    )

    assert {bucket: len(tracks) for bucket, tracks in result.items()} == {
        "similar": 2,
        "reverse": 2,
        "hidden": 2,
    }
    all_keys = [
        scoring._track_key(track) for tracks in result.values() for track in tracks
    ]
    assert len(all_keys) == len(set(all_keys))
    assert [bucket for bucket, _ in calls] == [
        "similar",
        "reverse",
        "hidden",
    ]
    assert [len(excluded) for _, excluded in calls] == [0, 2, 4]


async def test_direct_recommendations_continue_after_bucket_failure(monkeypatch):
    calls = []

    async def fake_similar(*args, **kwargs):
        calls.append("similar")
        return [TrackInfo(name="Similar", artist="Artist A")]

    async def fake_reverse(*args, **kwargs):
        calls.append("reverse")
        raise ValueError("invalid structured response")

    async def fake_hidden(*args, **kwargs):
        calls.append("hidden")
        return [TrackInfo(name="Hidden", artist="Artist C")]

    monkeypatch.setattr(
        "app.services.recommend_service.similar_listening_pattern", fake_similar
    )
    monkeypatch.setattr("app.services.recommend_service.reverse_top100", fake_reverse)
    monkeypatch.setattr(
        "app.services.recommend_service.hidden_discovery_by_artist", fake_hidden
    )

    result = await _run_direct_recommendations(
        "Seed",
        "Seed Artist",
        None,
        None,
        top_n=1,
        prefetched_similar=[],
    )

    assert calls == ["similar", "reverse", "hidden"]
    assert [track.name for track in result["similar"]] == ["Similar"]
    assert result["reverse"] == []
    assert [track.name for track in result["hidden"]] == ["Hidden"]


async def test_unresolved_direct_query_returns_empty_result_instead_of_raising(
    monkeypatch,
):
    """iTunes/Last.fm 모두 곡을 특정하지 못한 direct 쿼리가 500으로 새지 않아야 한다."""

    class StubGeminiWrapper:
        def __init__(self, *args, **kwargs):
            pass

    def fake_analyze(query, gemini_wrapper):
        return MusicQueryAnalysis(
            intent="direct",
            direct=DirectSearchAnalysis(
                search_query=query,
                alternative_queries=[],
            ),
        )

    async def fake_preprocess_input(*args, **kwargs):
        return None, None, None

    monkeypatch.setattr(
        "app.services.recommend_service.GeminiWrapper", StubGeminiWrapper
    )
    monkeypatch.setattr(
        "app.services.recommend_service.analyze_music_query", fake_analyze
    )
    monkeypatch.setattr(
        "app.services.recommend_service.preprocess_input", fake_preprocess_input
    )

    result = await run_recommend(
        "존재하지 않는 곡 제목",
        10,
        None,
        None,
        SimpleNamespace(gemini_api_key=None, gemini_model="test-model"),
    )

    assert result["result"] == {"similar": [], "reverse": [], "hidden": []}
    assert result["source_id"] is None
    assert result["album_art_url"] is None

    # 응답 스키마가 None을 거부하므로 계약 검증까지 통과해야 한다.
    response = RecommendResponse(**result)
    assert response.track_name == "존재하지 않는 곡 제목"
