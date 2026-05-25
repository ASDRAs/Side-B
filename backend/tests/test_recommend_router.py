from app.routers.recommend import RecommendRequest, RecommendResponse
from app.services.recommend_service import _pick_representative_track
from main import app
from recommend_algo import TrackInfo


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
