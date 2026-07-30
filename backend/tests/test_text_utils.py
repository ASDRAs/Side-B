from app.utils.text import compact_text, text_ratio


def test_compact_text_removes_dash():
    assert compact_text("k-pop") == "kpop"


def test_text_ratio_kpop_variants_equal():
    assert text_ratio("k-pop", "kpop") == 1.0


def test_text_ratio_substring_no_length_guard():
    assert text_ratio("abc", "xabcyz") == 0.9


def test_compact_text_removes_ampersand():
    assert compact_text("A&B") == "ab"


def test_text_ratio_ampersand_does_not_match_and():
    assert text_ratio("A&B", "a and b") < 0.9
