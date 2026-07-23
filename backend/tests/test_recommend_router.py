from app.routers.recommend import RecommendRequest, RecommendResponse
from app.services.recommend_service import (
    _pick_representative_track,
    _run_direct_recommendations,
)
from main import app
from recommend_algo import TrackInfo
from recommend_algo.common import scoring


def test_search_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/health" in paths
    assert "/search" in paths


def test_recommend_contract_uses_query_source_and_hidden_bucket():
    request = RecommendRequest(query="아이유 너랑나", top_n=3)
    response = RecommendResponse(
        track_name="너랑나",
        artist="IU",
        top_n=request.top_n,
        source_id="itunes:1",
        album_art_url="https://example.com/art.jpg",
        result={"similar": [], "reverse": [], "opposite": [], "hidden": []},
    )

    assert request.query == "아이유 너랑나"
    assert response.result["hidden"] == []
    assert response.source_id == "itunes:1"


def test_tag_fallback_seed_label_uses_representative_track():
    representative = TrackInfo(
        name="Under Caffeine",
        artist="Stella Jang",
        source_id="lastfm:under-caffeine",
        album_art_url="https://example.com/under-caffeine.jpg",
    )
    tag_results = {
        "similar": [representative],
        "reverse": [],
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
        "opposite": [
            TrackInfo(name="C", artist="Artist C"),
            TrackInfo(name="E", artist="Artist E"),
            TrackInfo(name="F", artist="Artist F"),
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
        "app.services.recommend_service.opposite_emotion",
        fake_runner("opposite"),
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
        None,
        top_n=2,
        prefetched_similar=[],
    )

    assert {bucket: len(tracks) for bucket, tracks in result.items()} == {
        "similar": 2,
        "reverse": 2,
        "opposite": 2,
        "hidden": 2,
    }
    all_keys = [
        scoring._track_key(track)
        for tracks in result.values()
        for track in tracks
    ]
    assert len(all_keys) == len(set(all_keys))
    assert [bucket for bucket, _ in calls] == [
        "similar",
        "reverse",
        "opposite",
        "hidden",
    ]
    assert [len(excluded) for _, excluded in calls] == [0, 2, 4, 6]
