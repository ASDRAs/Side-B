import re
from difflib import SequenceMatcher


def compact_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣ぁ-ゟ゠-ヿ一-鿿]+", "", value.lower())


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


def normalize_text(value: str) -> str:
    lowered = value.lower().replace("&", " and ")
    lowered = re.sub(r"[\(\)\[\]\{\},.:;!?'\"`~]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def compact_normalized(value: str) -> str:
    return re.sub(r"\s+", "", normalize_text(value))


def normalize_tag(tag: str) -> str:
    return re.sub(r"\s+", "-", str(tag).strip().lower())


def sim(a: str, b: str) -> float:
    normalized_a = normalize_text(a)
    normalized_b = normalize_text(b)
    compact_a = compact_normalized(a)
    compact_b = compact_normalized(b)
    if not normalized_a or not normalized_b:
        return 0.0
    if compact_a and compact_b and compact_a == compact_b:
        return 1.0
    if min(len(compact_a), len(compact_b)) >= 4 and (
        compact_a in compact_b or compact_b in compact_a
    ):
        return 1.0
    return SequenceMatcher(None, normalized_a, normalized_b).ratio()
