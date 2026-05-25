from app.utils.text import (
    compact_normalized,
    compact_text,
    normalize_text,
    sim,
    text_ratio,
)


def test_compact_text_removes_dash():
    assert compact_text("k-pop") == "kpop"


def test_compact_normalized_keeps_dash():
    assert compact_normalized("k-pop") == "k-pop"


def test_text_ratio_kpop_variants_equal():
    assert text_ratio("k-pop", "kpop") == 1.0


def test_sim_kpop_variants_not_exact():
    assert sim("k-pop", "kpop") < 1.0


def test_text_ratio_substring_no_length_guard():
    assert text_ratio("abc", "xabcyz") == 0.9


def test_sim_substring_requires_length_4():
    score = sim("abc", "xabcyz")
    assert score < 0.9


def test_normalize_text_expands_ampersand():
    assert normalize_text("A&B") == "a and b"


def test_compact_text_removes_ampersand():
    assert compact_text("A&B") == "ab"


def test_sim_ampersand_matches_and():
    assert sim("A&B", "a and b") == 1.0


def test_text_ratio_ampersand_does_not_match_and():
    assert text_ratio("A&B", "a and b") < 0.9
