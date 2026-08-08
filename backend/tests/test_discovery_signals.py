"""Last.fm raw 어댑터가 pylast가 버리는 필드를 보존하는지 확인한다.

PR 3은 필드만 보존한다. 점수와 추천 순서는 바꾸지 않는다.
"""

import pytest

from recommend_algo.common import lastfm_raw
from recommend_algo.common.models import DiscoverySignals, TrackInfo, track_to_api_dict
from tests.lastfm_fakes import ArtistTopTracksSource, SimilarTrackSource, tracks_xml


class _Named:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class _SimilarItem:
    def __init__(self, artist, title, match, playcount=None):
        self.item = _Track(artist, title)
        self.match = match
        if playcount is not None:
            self.playcount = playcount


class _Track:
    def __init__(self, artist, title):
        self._artist = _Named(artist)
        self._title = title

    def get_name(self):
        return self._title

    def get_artist(self):
        return self._artist


# ── artist.getTopTracks: listeners와 rank를 되찾는다 ──────────────


def test_artist_top_tracks_preserves_listeners_and_rank():
    """pylast는 playcount만 남기고 listeners와 rank를 버린다."""
    source = ArtistTopTracksSource("IU", ["Lilac", "Palette"], listeners=[511681, 300])

    items = lastfm_raw.artist_top_tracks(source, 2)

    assert [item.item.get_name() for item in items] == ["Lilac", "Palette"]
    assert [item.signals.global_listeners for item in items] == [511681, 300]
    assert [item.signals.source_rank for item in items] == [1, 2]
    assert all(item.signals.global_playcount is not None for item in items)
    assert all(item.signals.evidence_source == "artist.getTopTracks" for item in items)
    assert all(item.signals.source_group == "IU" for item in items)


def test_artist_top_tracks_has_no_similarity_score():
    """이 엔드포인트는 유사도를 주지 않는다. 0이 아니라 None이어야 한다."""
    source = ArtistTopTracksSource("IU", ["Lilac"])

    signals = lastfm_raw.artist_top_tracks(source, 1)[0].signals

    assert signals.similarity_match is None


def test_artist_top_tracks_makes_one_request():
    source = ArtistTopTracksSource("IU", [f"Track{i}" for i in range(20)])

    lastfm_raw.artist_top_tracks(source, 20)

    assert source.requests == ["artist.getTopTracks"]


# ── track.getSimilar: playcount를 되찾고 listeners는 없다 ─────────


def test_track_similar_preserves_playcount_and_match():
    source = SimilarTrackSource(
        [_SimilarItem("Radiohead", "Karma Police", 0.85, playcount=1234)],
        artist="Radiohead",
        title="Creep",
    )

    item = lastfm_raw.track_similar(source, 1)[0]

    assert item.item.get_name() == "Karma Police"
    assert item.item.get_artist().get_name() == "Radiohead"
    assert item.match == pytest.approx(0.85)
    assert item.signals.similarity_match == pytest.approx(0.85)
    assert item.signals.global_playcount == 1234
    assert item.signals.source_group == "Radiohead - Creep"


def test_track_similar_reports_missing_listeners_as_none():
    """이 경로에는 listeners가 없다. 후보마다 아티스트 통계를 새로 받으면
    금지한 fan-out이 다시 생기므로 비는 것이 정상이다."""
    source = SimilarTrackSource([_SimilarItem("Radiohead", "Karma Police", 0.85)])

    signals = lastfm_raw.track_similar(source, 1)[0].signals

    assert signals.global_listeners is None
    assert signals.source_rank is None


def test_track_similar_makes_one_request():
    source = SimilarTrackSource(
        [_SimilarItem("A", f"T{i}", 0.5) for i in range(60)],
    )

    lastfm_raw.track_similar(source, 60)

    assert source.requests == ["track.getSimilar"]


# ── 미제공을 0으로 만들지 않는다 ──────────────────────────────────
# pylast의 _number()는 None과 ""를 0으로 바꾼다. 0과 미제공을 구분하지 못하면
# 노출도 점수가 조용히 틀어진다.

MISSING_FIELD_CASES = [
    pytest.param("playcount", "global_playcount", id="playcount"),
    pytest.param("listeners", "global_listeners", id="listeners"),
]


@pytest.mark.parametrize("field,attribute", MISSING_FIELD_CASES)
def test_absent_numeric_field_is_none_not_zero(field, attribute):
    present = tracks_xml("toptracks", [{"name": "T", "artist": "A", field: 0}])
    absent = tracks_xml("toptracks", [{"name": "T", "artist": "A"}])

    with_zero = lastfm_raw._parse_tracks(present, "artist.getTopTracks", "A")[0]
    without = lastfm_raw._parse_tracks(absent, "artist.getTopTracks", "A")[0]

    assert getattr(with_zero.signals, attribute) == 0
    assert getattr(without.signals, attribute) is None


def test_rows_without_artist_are_skipped():
    doc = tracks_xml("toptracks", [{"name": "T", "artist": ""}])

    assert lastfm_raw._parse_tracks(doc, "artist.getTopTracks", "A") == []


def test_signals_of_tolerates_objects_without_signals():
    assert lastfm_raw.signals_of(object()) is None


# ── 응답에는 새지 않는다 ──────────────────────────────────────────


def test_signals_stay_internal():
    """PR 3은 관측 가능한 응답을 바꾸지 않는다."""
    track = TrackInfo(
        name="Lilac",
        artist="IU",
        signals=DiscoverySignals(global_listeners=1, evidence_source="x"),
    )

    assert "signals" not in track_to_api_dict(track)
