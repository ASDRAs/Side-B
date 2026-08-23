import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.youtube_export import match_youtube_tracks
from app.schemas.youtube_export import YouTubeMatchRequest
from app.services.access import BackendAccess
from app.services.youtube.client import (
    YouTubeConfigurationError,
    YouTubeQuotaExceededError,
)
from app.services.youtube.matcher import MatchOutcome, YouTubeMatch
from main import app


class _Matcher:
    def __init__(self, outcomes=None, error=None):
        self.outcomes = outcomes or {}
        self.error = error
        self.calls = []

    async def match_track(self, name, artist):
        self.calls.append((name, artist))
        if self.error:
            raise self.error
        return self.outcomes[(name, artist)]


def _request(matcher):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                youtube_matcher=matcher,
                youtube_export_access=BackendAccess("test-token"),
            )
        )
    )


def test_youtube_match_route_is_registered():
    assert "/exports/youtube/matches" in {route.path for route in app.routes}


def test_request_strips_text_and_rejects_blank_track_fields():
    request = YouTubeMatchRequest(
        bucket="similar",
        tracks=[{"name": "  Hello ", "artist": " Adele  "}],
    )
    assert request.tracks[0].name == "Hello"
    assert request.tracks[0].artist == "Adele"

    with pytest.raises(ValidationError):
        YouTubeMatchRequest(
            bucket="similar",
            tracks=[{"name": "   ", "artist": "Adele"}],
        )


async def test_router_deduplicates_tracks_and_preserves_original_positions():
    matcher = _Matcher(
        {
            ("Hello", "Adele"): MatchOutcome(
                match=YouTubeMatch(
                    video_id="hello-id",
                    youtube_title="Hello",
                    channel_title="Adele - Topic",
                    confidence=1.0,
                )
            ),
            ("Missing", "Unknown"): MatchOutcome(match=None, reason="not_found"),
        }
    )
    req = YouTubeMatchRequest(
        bucket="similar",
        tracks=[
            {"name": "Hello", "artist": "Adele"},
            {"name": " hello ", "artist": "ADELE"},
            {"name": "Missing", "artist": "Unknown"},
        ],
    )

    response = await match_youtube_tracks(req, _request(matcher), "test-token")

    assert response.requested == 3
    assert response.deduplicated == 1
    assert matcher.calls == [("Hello", "Adele"), ("Missing", "Unknown")]
    assert response.matched[0].position == 0
    assert response.unmatched[0].position == 2


async def test_router_deduplicates_matches_that_resolve_to_the_same_video():
    shared_video = YouTubeMatch(
        video_id="same-video",
        youtube_title="Shared upload",
        channel_title="Official Channel",
        confidence=0.95,
    )
    matcher = _Matcher(
        {
            ("Track A", "Artist A"): MatchOutcome(match=shared_video),
            ("Track B", "Artist B"): MatchOutcome(match=shared_video),
        }
    )
    req = YouTubeMatchRequest(
        bucket="similar",
        tracks=[
            {"name": "Track A", "artist": "Artist A"},
            {"name": "Track B", "artist": "Artist B"},
        ],
    )

    response = await match_youtube_tracks(req, _request(matcher), "test-token")

    assert [track.name for track in response.matched] == ["Track A"]
    assert response.unmatched[0].reason == "duplicate_video"
    assert response.unmatched[0].position == 1


async def test_router_returns_low_confidence_candidate_for_manual_review():
    candidate = YouTubeMatch(
        video_id="translated-topic",
        youtube_title="If I could be a constellation",
        channel_title="kessoku band - Topic",
        confidence=0.47,
    )
    matcher = _Matcher(
        {
            ("星座になれたら", "kessoku band"): MatchOutcome(
                match=candidate,
                reason="low_confidence",
            )
        }
    )
    req = YouTubeMatchRequest(
        bucket="similar",
        tracks=[{"name": "星座になれたら", "artist": "kessoku band"}],
    )

    response = await match_youtube_tracks(req, _request(matcher), "test-token")

    assert response.unmatched == []
    assert response.matched[0].video_id == "translated-topic"
    assert response.matched[0].auto_selected is False


async def test_router_returns_below_review_threshold_as_unmatched():
    matcher = _Matcher(
        {
            ("Hello", "Adele"): MatchOutcome(
                match=None,
                reason="low_confidence",
            )
        }
    )
    req = YouTubeMatchRequest(
        bucket="similar",
        tracks=[{"name": "Hello", "artist": "Adele"}],
    )

    response = await match_youtube_tracks(req, _request(matcher), "test-token")

    assert response.matched == []
    assert response.unmatched[0].reason == "low_confidence"


async def test_router_cancels_sibling_searches_when_one_fails():
    class _FailingMatcher:
        def __init__(self):
            self.cancelled = []

        async def match_track(self, name, artist):
            if name == "Quota":
                await asyncio.sleep(0)
                raise YouTubeQuotaExceededError()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(name)
                raise

    matcher = _FailingMatcher()
    req = YouTubeMatchRequest(
        bucket="hidden",
        tracks=[
            {"name": "Quota", "artist": "Artist"},
            {"name": "Sibling A", "artist": "Artist"},
            {"name": "Sibling B", "artist": "Artist"},
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await match_youtube_tracks(req, _request(matcher), "test-token")

    assert exc_info.value.detail["code"] == "youtube_quota_exceeded"
    assert set(matcher.cancelled) == {"Sibling A", "Sibling B"}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (YouTubeConfigurationError(), "youtube_configuration_error"),
        (YouTubeQuotaExceededError(), "youtube_quota_exceeded"),
    ],
)
async def test_router_maps_service_errors_to_typed_503(error, code):
    req = YouTubeMatchRequest(
        bucket="hidden",
        tracks=[{"name": "Hello", "artist": "Adele"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        await match_youtube_tracks(req, _request(_Matcher(error=error)), "test-token")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == code


async def test_router_rejects_missing_or_invalid_export_token():
    matcher = _Matcher()
    req = YouTubeMatchRequest(
        bucket="hidden",
        tracks=[{"name": "Hello", "artist": "Adele"}],
    )

    for token in (None, "wrong-token"):
        with pytest.raises(HTTPException) as exc_info:
            await match_youtube_tracks(req, _request(matcher), token)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["code"] == "youtube_export_unauthorized"
    assert matcher.calls == []


async def test_router_rate_limits_authenticated_export_requests():
    access = BackendAccess("test-token", requests_per_minute=1)
    matcher = _Matcher(
        {("Hello", "Adele"): MatchOutcome(match=None, reason="not_found")}
    )
    request = _request(matcher)
    request.app.state.youtube_export_access = access
    req = YouTubeMatchRequest(
        bucket="hidden",
        tracks=[{"name": "Hello", "artist": "Adele"}],
    )

    await match_youtube_tracks(req, request, "test-token")
    with pytest.raises(HTTPException) as exc_info:
        await match_youtube_tracks(req, request, "test-token")

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "60"
