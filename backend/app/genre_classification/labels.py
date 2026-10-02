from collections.abc import Iterable

from app.genre_classification.errors import GenreModelLoadError


# Model labels are training artifacts. API IDs are a stable contract shared with
# clients and must not change when a label encoder is replaced.
MODEL_LABEL_TO_GENRE_ID = {
    "POP": "pop",
    "R&B/Soul": "rnb_soul",
    "댄스": "dance",
    "랩/힙합": "hiphop",
    "록/메탈": "rock_metal",
    "발라드": "ballad",
    "블루스": "blues",
    "재즈": "jazz",
    "컨트리": "country",
    "포크": "folk",
    # Previous model labels remain valid while inference revisions roll over.
    "pop": "pop",
    "rnb_soul": "rnb_soul",
    "dance": "dance",
    "hiphop": "hiphop",
    "rock_metal": "rock_metal",
    "ballad": "ballad",
    "jazz": "jazz",
    "jpop": "jpop",
    "folk_blues_country": "folk_blues_country",
}


def genre_id_for_model_label(label: object) -> str:
    model_label = str(label)
    try:
        return MODEL_LABEL_TO_GENRE_ID[model_label]
    except KeyError as exc:
        raise GenreModelLoadError(
            f"label encoder contains an unsupported genre label: {model_label!r}"
        ) from exc


def validate_model_labels(labels: Iterable[object]) -> None:
    for label in labels:
        genre_id_for_model_label(label)
