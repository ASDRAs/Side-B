import re
from pathlib import Path

from app.genre_classification.labels import MODEL_LABEL_TO_GENRE_ID


ROOT = Path(__file__).resolve().parents[2]


def test_extension_supports_every_genre_id_the_inference_api_can_return():
    source = (ROOT / "extension" / "scripts" / "eqPresets.js").read_text(
        encoding="utf-8"
    )
    match = re.search(r"const genreGains = \{(?P<body>.*?)\n  \};", source, re.DOTALL)
    assert match, "eqPresets.js no longer exposes the expected genre preset table"

    preset_ids = set(
        re.findall(r'^\s{4}(?:"([^"]+)"|([a-z][a-z0-9_]*)):', match.group("body"), re.MULTILINE)
    )
    flattened_preset_ids = {quoted or bare for quoted, bare in preset_ids}

    assert flattened_preset_ids == set(MODEL_LABEL_TO_GENRE_ID.values())
