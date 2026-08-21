import asyncio

import pytest

from app.services.youtube.client import YouTubeAPIUnavailableError
from app.services.youtube.matcher import (
    YouTubeMatcher,
    score_candidate,
    select_best_candidate,
)


def _item(video_id, title, channel):
    return {
        "id": {"videoId": video_id},
        "snippet": {"title": title, "channelTitle": channel},
    }


def test_topic_upload_of_the_expected_track_scores_above_threshold():
    candidate = score_candidate(
        _item("topic-id", "Event Horizon", "YOUNHA - Topic"),
        "Event Horizon",
        "YOUNHA",
    )

    assert candidate is not None
    assert candidate.confidence >= 0.85


def test_official_video_can_take_artist_credit_from_title_prefix():
    candidate = score_candidate(
        _item(
            "mv-id",
            "YOUNHA - Event Horizon (Official Music Video)",
            "Stone Music Entertainment",
        ),
        "Event Horizon",
        "YOUNHA",
    )

    assert candidate is not None
    assert candidate.confidence >= 0.85


@pytest.mark.parametrize(
    ("title", "channel", "expected_title", "expected_artist"),
    [
        (
            "NewJeans (뉴진스) 'Super Shy' Official MV",
            "HYBE LABELS",
            "Super Shy",
            "NewJeans",
        ),
        ("aespa 에스파 'Whiplash' MV", "SMTOWN", "Whiplash", "aespa"),
        ("IU(아이유) _ Love wins all", "1theK (원더케이)", "Love wins all", "IU"),
        ("Adele - Hello", "AdeleVEVO", "Hello", "Adele"),
    ],
)
def test_korean_and_vevo_title_formats_have_a_safe_threshold_margin(
    title, channel, expected_title, expected_artist
):
    candidate = score_candidate(
        _item("video-id", title, channel), expected_title, expected_artist
    )

    assert candidate is not None
    assert candidate.confidence >= 0.88


def test_wrong_artist_and_derivative_versions_stay_below_threshold():
    wrong_artist = score_candidate(
        _item("wrong", "Hello", "Definitely Not Adele - Topic"),
        "Hello",
        "Adele",
    )
    cover = score_candidate(
        _item("cover", "Adele - Hello (Cover)", "Cover Studio Official"),
        "Hello",
        "Adele",
    )

    assert wrong_artist is not None
    assert wrong_artist.confidence < 0.85
    assert cover is not None
    assert cover.confidence < 0.85


def test_same_script_artist_prefix_does_not_count_as_an_alias():
    candidate = score_candidate(
        _item(
            "tribute",
            "Adele Tribute - Hello (Official Audio)",
            "Music Archive",
        ),
        "Hello",
        "Adele",
    )

    assert candidate is not None
    assert candidate.confidence < 0.85


def test_intrinsic_live_title_rejects_an_unrequested_live_version():
    candidate = score_candidate(
        _item(
            "live-version",
            "Oasis - Live Forever (Live)",
            "Oasis - Topic",
        ),
        "Live Forever",
        "Oasis",
    )

    assert candidate is not None
    assert candidate.confidence < 0.85


def test_requested_acoustic_version_rejects_the_original_recording():
    candidate = score_candidate(
        _item("original", "Artist - Song", "Artist - Topic"),
        "Song (Acoustic)",
        "Artist",
    )

    assert candidate is not None
    assert candidate.confidence < 0.85


@pytest.mark.parametrize(
    ("candidate_title", "expected_title"),
    [
        ("Artist - Intro (Part 2)", "Intro (Part 1)"),
        ("Artist - Song (Japanese Version)", "Song (Korean Version)"),
    ],
)
def test_meaningful_parenthetical_versions_do_not_collapse_to_the_same_track(
    candidate_title, expected_title
):
    candidate = score_candidate(
        _item("wrong-version", candidate_title, "Artist - Topic"),
        expected_title,
        "Artist",
    )

    assert candidate is not None
    assert candidate.confidence < 0.85


def test_title_word_cover_does_not_hide_an_actual_cover_version():
    original = score_candidate(
        _item("original", "Bruce Springsteen - Cover Me", "BruceSpringsteenVEVO"),
        "Cover Me",
        "Bruce Springsteen",
    )
    cover = score_candidate(
        _item(
            "cover",
            "Bruce Springsteen - Cover Me (Cover)",
            "Cover Studio",
        ),
        "Cover Me",
        "Bruce Springsteen",
    )

    assert original is not None and original.confidence >= 0.88
    assert cover is not None and cover.confidence < 0.85


def test_artist_name_marker_does_not_penalize_the_official_channel():
    candidate = score_candidate(
        _item("official", "Cover Drive - Twilight", "Cover Drive - Topic"),
        "Twilight",
        "Cover Drive",
    )

    assert candidate is not None
    assert candidate.confidence >= 0.88


def test_channel_words_do_not_trigger_derivative_penalties():
    live_nation = score_candidate(
        _item("live-nation", "Adele - Hello (Official Video)", "Live Nation"),
        "Hello",
        "Adele",
    )
    discovery = score_candidate(
        _item(
            "discovery",
            "Adele - Hello (Official Video)",
            "Discovery Music Source",
        ),
        "Hello",
        "Adele",
    )
    mr_probz = score_candidate(
        _item("mr-probz", "Mr. Probz - Waves", "Mr. Probz - Topic"),
        "Waves",
        "Mr. Probz",
    )

    assert live_nation is not None and live_nation.confidence >= 0.88
    assert discovery is not None and discovery.confidence >= 0.88
    assert mr_probz is not None and mr_probz.confidence >= 0.88


@pytest.mark.parametrize(
    ("title", "expected_title", "expected_artist"),
    [
        (
            "THE KILLERS - MR. BRIGHTSIDE (OFFICIAL VIDEO)",
            "Mr. Brightside",
            "The Killers",
        ),
        ("ELO - MR. BLUE SKY", "Mr. Blue Sky", "ELO"),
        ("소녀시대 (Girls Generation) - MR.TAXI", "Mr. Taxi", "소녀시대"),
    ],
)
def test_uppercase_mr_in_track_names_is_not_treated_as_instrumental(
    title, expected_title, expected_artist
):
    candidate = score_candidate(
        _item("original", title, "Archive"), expected_title, expected_artist
    )

    assert candidate is not None
    assert candidate.confidence >= 0.88


@pytest.mark.parametrize(
    ("title", "channel", "expected_title", "expected_artist"),
    [
        ("Adele - Hello", "Sing King Karaoke", "Hello", "Adele"),
        ("아이유 - 밤편지", "뮤직마루 MusicMaru", "밤편지", "아이유"),
        ("Adele - Hello", "Adele Tribute Channel", "Hello", "Adele"),
        ("Adele - Hello [MR]", "Archive", "Hello", "Adele"),
    ],
)
def test_derivative_markers_in_title_or_channel_stay_below_threshold(
    title, channel, expected_title, expected_artist
):
    candidate = score_candidate(
        _item("derivative", title, channel), expected_title, expected_artist
    )

    assert candidate is not None
    assert candidate.confidence < 0.85


def test_best_candidate_is_selected_instead_of_first_result():
    items = [
        _item("cover", "Hello (Cover)", "Adele Covers"),
        _item("topic", "Hello", "Adele - Topic"),
    ]

    match = select_best_candidate(items, "Hello", "Adele")

    assert match is not None
    assert match.video_id == "topic"


class _FakeSearchClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    async def search(self, name, artist):
        self.calls.append((name, artist))
        return self.items


async def test_matcher_caches_positive_result_by_normalized_identity():
    client = _FakeSearchClient([_item("topic", "Hello", "Adele - Topic")])
    matcher = YouTubeMatcher(client)

    first = await matcher.match_track("Hello", "Adele")
    second = await matcher.match_track(" hello ", "ADELE")

    assert first.match is not None
    assert second == first
    assert client.calls == [("Hello", "Adele")]


async def test_matcher_caches_empty_search_as_not_found():
    client = _FakeSearchClient([])
    matcher = YouTubeMatcher(client)

    first = await matcher.match_track("Missing", "Unknown")
    second = await matcher.match_track("Missing", "Unknown")

    assert first.match is None
    assert first.reason == "not_found"
    assert second == first
    assert len(client.calls) == 1


async def test_matcher_marks_existing_but_weak_candidates_low_confidence():
    client = _FakeSearchClient(
        [_item("wrong", "Another Song", "Another Artist - Topic")]
    )
    matcher = YouTubeMatcher(client)

    outcome = await matcher.match_track("Hello", "Adele")

    assert outcome.match is None
    assert outcome.reason == "low_confidence"


async def test_matcher_marks_unparseable_search_items_as_unusable():
    client = _FakeSearchClient(
        [
            {"id": {"videoId": "missing-snippet"}},
            {"snippet": {"title": "missing-id", "channelTitle": "Channel"}},
        ]
    )

    outcome = await YouTubeMatcher(client).match_track("Hello", "Adele")

    assert outcome.match is None
    assert outcome.reason == "unusable_result"


async def test_matcher_shares_one_inflight_search_for_the_same_track():
    class _SlowClient(_FakeSearchClient):
        async def search(self, name, artist):
            self.calls.append((name, artist))
            await asyncio.sleep(0.01)
            return self.items

    client = _SlowClient([])
    matcher = YouTubeMatcher(client)

    outcomes = await asyncio.gather(
        *(matcher.match_track("Hello", "Adele") for _ in range(5))
    )

    assert len(client.calls) == 1
    assert {outcome.reason for outcome in outcomes} == {"not_found"}


async def test_matcher_does_not_cache_or_retain_failed_inflight_searches():
    class _FailOnceClient(_FakeSearchClient):
        async def search(self, name, artist):
            self.calls.append((name, artist))
            if len(self.calls) == 1:
                raise YouTubeAPIUnavailableError()
            return []

    client = _FailOnceClient([])
    matcher = YouTubeMatcher(client)

    with pytest.raises(YouTubeAPIUnavailableError):
        await matcher.match_track("Hello", "Adele")
    outcome = await matcher.match_track("Hello", "Adele")

    assert outcome.reason == "not_found"
    assert len(client.calls) == 2


async def test_matcher_expires_positive_cache_entries_with_injected_clock():
    now = [100.0]
    client = _FakeSearchClient([_item("topic", "Hello", "Adele - Topic")])
    matcher = YouTubeMatcher(
        client,
        positive_ttl_seconds=10,
        clock=lambda: now[0],
    )

    await matcher.match_track("Hello", "Adele")
    now[0] += 11
    await matcher.match_track("Hello", "Adele")

    assert len(client.calls) == 2


async def test_matcher_cache_uses_lru_eviction():
    client = _FakeSearchClient([])
    matcher = YouTubeMatcher(client, max_cache_size=2)

    await matcher.match_track("A", "Artist")
    await matcher.match_track("B", "Artist")
    await matcher.match_track("A", "Artist")
    await matcher.match_track("C", "Artist")
    await matcher.match_track("B", "Artist")

    assert client.calls == [
        ("A", "Artist"),
        ("B", "Artist"),
        ("C", "Artist"),
        ("B", "Artist"),
    ]


async def test_matcher_limits_concurrent_searches():
    class _ConcurrencyClient(_FakeSearchClient):
        def __init__(self):
            super().__init__([])
            self.active = 0
            self.max_active = 0

        async def search(self, name, artist):
            self.calls.append((name, artist))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return []

    client = _ConcurrencyClient()
    matcher = YouTubeMatcher(client, concurrency=2)

    await asyncio.gather(
        *(matcher.match_track(f"Track {index}", "Artist") for index in range(5))
    )

    assert client.max_active == 2


async def test_matcher_cancels_the_search_when_its_last_waiter_is_cancelled():
    class _BlockingClient(_FakeSearchClient):
        def __init__(self):
            super().__init__([])
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def search(self, name, artist):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    client = _BlockingClient()
    matcher = YouTubeMatcher(client)
    waiter = asyncio.create_task(matcher.match_track("Hello", "Adele"))
    await client.started.wait()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.wait_for(client.cancelled.wait(), timeout=1)

    assert matcher._inflight == {}


async def test_cancelling_one_shared_waiter_keeps_the_other_waiter_alive():
    class _ControlledClient(_FakeSearchClient):
        def __init__(self):
            super().__init__([])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def search(self, name, artist):
            self.calls.append((name, artist))
            self.started.set()
            await self.release.wait()
            return []

    client = _ControlledClient()
    matcher = YouTubeMatcher(client)
    first = asyncio.create_task(matcher.match_track("Hello", "Adele"))
    second = asyncio.create_task(matcher.match_track("Hello", "Adele"))
    await client.started.wait()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    client.release.set()
    outcome = await second

    assert outcome.reason == "not_found"
    assert len(client.calls) == 1
