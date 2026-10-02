import pytest

from app.genre_classification.labels import (
    genre_id_for_model_label,
    validate_model_labels,
)
from app.genre_classification.errors import GenreModelLoadError


@pytest.mark.parametrize(
    ("model_label", "genre_id"),
    [
        ("POP", "pop"),
        ("R&B/Soul", "rnb_soul"),
        ("댄스", "dance"),
        ("랩/힙합", "hiphop"),
        ("록/메탈", "rock_metal"),
        ("발라드", "ballad"),
        ("블루스", "blues"),
        ("재즈", "jazz"),
        ("컨트리", "country"),
        ("포크", "folk"),
    ],
)
def test_current_model_labels_map_to_stable_api_ids(model_label, genre_id):
    assert genre_id_for_model_label(model_label) == genre_id


def test_previous_model_labels_remain_valid_during_rolling_deployment():
    validate_model_labels(
        [
            "pop",
            "rnb_soul",
            "dance",
            "hiphop",
            "rock_metal",
            "ballad",
            "jazz",
            "jpop",
            "folk_blues_country",
        ]
    )


def test_unknown_model_label_fails_startup_contract_validation():
    with pytest.raises(GenreModelLoadError, match="unsupported genre label"):
        validate_model_labels(["future_unregistered_label"])
