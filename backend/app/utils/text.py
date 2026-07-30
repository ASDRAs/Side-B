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
