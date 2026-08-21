import unicodedata
from difflib import SequenceMatcher


def _fold_latin_diacritics(char: str) -> str:
    """Fold Latin accents without stripping marks from Hangul or kana."""
    decomposed = unicodedata.normalize("NFD", char)
    base = next(
        (part for part in decomposed if not unicodedata.category(part).startswith("M")),
        char,
    )
    if "LATIN" not in unicodedata.name(base, ""):
        return char
    return "".join(
        part for part in decomposed if not unicodedata.category(part).startswith("M")
    )


def compact_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    folded = "".join(_fold_latin_diacritics(char) for char in normalized)
    return "".join(char for char in folded if char.isalnum())


def text_ratio(left: str, right: str) -> float:
    left_norm = compact_text(left)
    right_norm = compact_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.9
    return SequenceMatcher(None, left_norm, right_norm).ratio()
