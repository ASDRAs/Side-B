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


def test_compact_text_folds_latin_diacritics_and_fullwidth_forms():
    assert compact_text("Beyoncé") == "beyonce"
    assert compact_text("Björk") == "bjork"
    assert compact_text("Sigur Rós") == "sigurros"
    assert compact_text("Déjà Vu") == "dejavu"
    assert compact_text("Mötley Crüe") == "motleycrue"
    assert compact_text("ＡＤＥＬＥ") == "adele"
    assert compact_text("ＩＵ") == "iu"


def test_compact_text_preserves_hangul_and_voiced_kana_distinctions():
    assert compact_text("가나다") == "가나다"
    assert compact_text("ガラス") == "ガラス"
    assert compact_text("カラス") == "カラス"
    assert compact_text("パンダ") == "パンダ"
    assert compact_text("ハンダ") == "ハンダ"
    assert compact_text("ガラス") != compact_text("カラス")
    assert compact_text("パンダ") != compact_text("ハンダ")
