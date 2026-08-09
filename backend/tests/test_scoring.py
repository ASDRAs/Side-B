import pytest

from recommend_algo.common import scoring
from recommend_algo.common.models import DiscoverySignals, TrackInfo


def _track(name, **signal_fields):
    signals = DiscoverySignals(**signal_fields) if signal_fields else None
    return TrackInfo(name=name, artist="Artist", signals=signals)


# ── 신호 선택 우선순위 ────────────────────────────────────────────


def test_listeners_wins_over_playcount():
    """listeners만이 아티스트 간 비교가 된다. 있으면 그걸 쓴다."""
    track = _track("A", global_listeners=100, global_playcount=999_999)

    assert scoring._exposure_value(track) == (100.0, "listeners")


def test_playcount_is_used_when_listeners_is_absent():
    """track.getSimilar 경로. listeners가 없다."""
    track = _track("A", global_playcount=500)

    assert scoring._exposure_value(track) == (500.0, "playcount")


def test_tag_rank_is_the_last_resort_and_is_inverted():
    """tag.getTopTracks는 순위만 준다. 순위는 작을수록 노출이 크다."""
    track = _track("A", source_rank=3, source_group="k-pop")

    assert scoring._exposure_value(track) == (-3.0, "tag_rank")


def test_no_signals_at_all():
    assert scoring._exposure_value(TrackInfo(name="A", artist="B")) == (None, "none")


# ── 풀 내부 백분위 ────────────────────────────────────────────────


def test_exposure_is_ranked_within_the_pool():
    tracks = [
        _track("low", global_listeners=10),
        _track("mid", global_listeners=500),
        _track("high", global_listeners=10_000),
    ]

    scoring.assign_exposure(tracks)

    assert [t.exposure_score for t in tracks] == [0.0, 0.5, 1.0]
    assert all(t.exposure_source == "listeners" for t in tracks)


def test_equal_values_get_equal_scores():
    tracks = [
        _track("a", global_listeners=10),
        _track("b", global_listeners=10),
        _track("c", global_listeners=99),
    ]

    scoring.assign_exposure(tracks)

    assert tracks[0].exposure_score == tracks[1].exposure_score == 0.0
    assert tracks[2].exposure_score == 1.0


def test_single_candidate_is_neutral():
    """비교 대상이 없으면 순위를 매길 수 없다. 최저 노출로 오해하면 안 된다."""
    tracks = [_track("only", global_listeners=10)]

    scoring.assign_exposure(tracks)

    assert tracks[0].exposure_score == 0.5


def test_all_tied_group_is_neutral_not_maximally_obscure():
    """전부 같은 값이면 서로를 구분할 수 없다. 후보 하나일 때와 같아야 한다.

    "더 작은 값의 개수"로 세면 전원이 0이 되어, 아무 근거 없이 전원이 최대
    비주류로 판정된다.
    """
    tracks = [_track(f"x{i}", global_listeners=500) for i in range(5)]

    scoring.assign_exposure(tracks)

    assert [t.exposure_score for t in tracks] == [0.5] * 5
    assert all(scoring.obscurity_of(t) == 0.5 for t in tracks)


# ── 같은 곡이 두 경로에서 왔을 때 ─────────────────────────────────


def test_stronger_signal_wins_regardless_of_discovery_order():
    """발견 순서로 고르면 먼저 도는 경로의 약한 신호가 이긴다.

    reverse가 정확히 그 구조다. pool_a(track.getSimilar, playcount)를
    pool_b(artist.getTopTracks, listeners)보다 먼저 훑는다.
    """
    playcount_only = _track("dup", global_playcount=999)
    with_listeners = _track("dup", global_listeners=42)

    assert scoring.prefers_signals_of(with_listeners, playcount_only)
    assert not scoring.prefers_signals_of(playcount_only, with_listeners)


def test_equal_strength_signals_do_not_swap():
    """같은 등급이면 먼저 잡힌 것을 유지한다. 무의미한 교체를 만들지 않는다."""
    first = _track("dup", global_playcount=1)
    second = _track("dup", global_playcount=2)

    assert not scoring.prefers_signals_of(second, first)


def test_any_signal_beats_no_signal():
    tracks = _track("dup", source_rank=5, source_group="k-pop")
    nothing = TrackInfo(name="dup", artist="Artist")

    assert scoring.prefers_signals_of(tracks, nothing)


def test_listeners_and_playcount_are_ranked_separately():
    """자릿수가 다르다. 섞으면 playcount 후보가 전부 상위를 차지한다.

    reverse 버킷이 정확히 이 혼합 풀이다.
    """
    tracks = [
        _track("l-low", global_listeners=10),
        _track("l-high", global_listeners=20),
        _track("p-low", global_playcount=1_000_000),
        _track("p-high", global_playcount=2_000_000),
    ]

    scoring.assign_exposure(tracks)

    by_name = {t.name: t.exposure_score for t in tracks}
    assert by_name["l-low"] == 0.0 and by_name["l-high"] == 1.0
    assert by_name["p-low"] == 0.0 and by_name["p-high"] == 1.0


def test_tag_rank_is_ranked_per_tag():
    """태그가 다르면 같은 5위라도 절대 노출도가 전혀 다르다."""
    tracks = [
        _track("kpop-1", source_rank=1, source_group="k-pop"),
        _track("kpop-9", source_rank=9, source_group="k-pop"),
        _track("citypop-1", source_rank=1, source_group="citypop"),
        _track("citypop-9", source_rank=9, source_group="citypop"),
    ]

    scoring.assign_exposure(tracks)

    by_name = {t.name: t.exposure_score for t in tracks}
    assert by_name["kpop-1"] == 1.0 and by_name["kpop-9"] == 0.0
    assert by_name["citypop-1"] == 1.0 and by_name["citypop-9"] == 0.0


def test_missing_signal_stays_none():
    """기본값을 채우면 그 값이 저노출 판정에 끼어든다.

    예전 `popularity=None -> 55 -> 저노출 보너스`가 그 사고였다.
    """
    tracks = [_track("known", global_listeners=10), TrackInfo(name="?", artist="A")]

    scoring.assign_exposure(tracks)

    assert tracks[1].exposure_score is None
    assert tracks[1].exposure_source == "none"


def test_assign_exposure_resets_previous_values():
    """풀이 바뀌면 백분위도 바뀐다. 이전 계산이 남으면 안 된다."""
    track = _track("a", global_listeners=10)
    scoring.assign_exposure([track, _track("b", global_listeners=99)])
    assert track.exposure_score == 0.0

    scoring.assign_exposure([track])

    assert track.exposure_score == 0.5


# ── obscurity ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exposure,expected", [(0.0, 1.0), (0.25, 0.75), (1.0, 0.0)]
)
def test_obscurity_is_the_inverse_of_exposure(exposure, expected):
    track = TrackInfo(name="A", artist="B")
    track.exposure_score = exposure

    assert scoring.obscurity_of(track) == pytest.approx(expected)


def test_unknown_exposure_is_neutral_not_obscure():
    """확인되지 않은 곡이 저노출 보너스를 받으면 안 된다."""
    track = TrackInfo(name="A", artist="B")

    assert scoring.obscurity_of(track) == scoring.UNKNOWN_OBSCURITY
    assert scoring.UNKNOWN_OBSCURITY == 0.5
