import pytest

from recommend_algo.common import scoring
from recommend_algo.common.models import TrackInfo


def test_popularity_obscurity_uses_policy_ceiling():
    assert scoring._popularity_obscurity(
        15,
        ceiling=scoring.OBSCURITY_CEILING,
    ) == pytest.approx(0.8125)


def test_popularity_obscurity_defaults_missing_popularity():
    expected = (
        scoring.OBSCURITY_CEILING - scoring.DEFAULT_POPULARITY
    ) / scoring.OBSCURITY_CEILING
    assert scoring._popularity_obscurity(
        None,
        ceiling=scoring.OBSCURITY_CEILING,
    ) == pytest.approx(expected)


def test_popularity_obscurity_is_clamped():
    assert (
        scoring._popularity_obscurity(
            100,
            ceiling=scoring.OBSCURITY_CEILING,
        )
        == 0.0
    )
    assert (
        scoring._popularity_obscurity(
            -10,
            ceiling=scoring.OBSCURITY_CEILING,
        )
        == 1.0
    )


def test_low_exposure_and_resolved_popularity_share_default():
    track = TrackInfo(name="Unknown", artist="Artist")

    assert scoring._resolved_popularity(track) == scoring.DEFAULT_POPULARITY
    assert scoring._is_low_exposure(track.popularity)
    assert scoring._is_low_exposure(scoring.LOW_EXPOSURE_CUTOFF - 1)
    assert not scoring._is_low_exposure(scoring.LOW_EXPOSURE_CUTOFF)
