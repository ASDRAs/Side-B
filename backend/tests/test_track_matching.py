from app.utils.track_matching import (
    artist_ratio,
    artist_score,
    clean_title,
    identity_qualifiers_match,
    is_decorative_remainder,
    looks_like_bad_version,
    strict_title_ratio,
    version_markers,
)


def test_public_matching_utilities_keep_catalog_behavior():
    assert clean_title("봄날 - 2017 Remaster") == "봄날"
    assert looks_like_bad_version("Hello (Karaoke Version)")
    assert looks_like_bad_version("Hello [MR]")
    assert looks_like_bad_version("Hello [MR/노래방]")
    assert looks_like_bad_version("Hello - MR")
    assert not looks_like_bad_version("Mr. Probz - Waves")
    assert not looks_like_bad_version("THE KILLERS - MR. BRIGHTSIDE")
    assert not looks_like_bad_version("Cover Me")
    assert looks_like_bad_version("Cover Me (Cover)")
    assert looks_like_bad_version("Cover Studio", title_context=False)
    assert strict_title_ratio("One", "Once") == 0.0
    assert artist_score("IU & SUGA", ("IU",)) == 1.0
    assert artist_ratio("Definitely Not Adele", "Adele") < 0.8


def test_artist_score_does_not_treat_punctuation_only_artist_as_missing():
    assert artist_score("Wrong", ("!!!",)) == 0.0
    assert artist_score("!!!", ("!!!",)) == 1.0


def test_identity_qualifiers_preserve_meaningful_parenthetical_versions():
    assert identity_qualifiers_match("Hello (Official Video)", "Hello")
    assert identity_qualifiers_match("Creep (Acoustic)", "Creep")
    assert identity_qualifiers_match("너랑 나 (YOU&I)", "너랑 나")
    assert not identity_qualifiers_match("Intro (Part 2)", "Intro (Part 1)")
    assert not identity_qualifiers_match(
        "Song (Japanese Version)", "Song (Korean Version)"
    )
    assert strict_title_ratio("Intro (Part 2)", "Intro (Part 1)") == 0.0


def test_strict_title_ratio_folds_diacritics_before_short_title_guard():
    assert strict_title_ratio("Björk", "Bjork") == 1.0
    assert strict_title_ratio("Déjà Vu", "Deja Vu") == 1.0
    assert strict_title_ratio("Zoë", "Zoe") == 1.0


def test_youtube_korean_markers_do_not_expand_shared_bad_version_rules():
    assert not looks_like_bad_version("아이유 밤편지 커버")
    assert not looks_like_bad_version("아이유 밤편지 불러봄")
    assert not looks_like_bad_version("아이유 밤편지 리메이크")
    assert not looks_like_bad_version("커버곡 모음집")


def test_is_decorative_remainder_accepts_only_non_identity_suffixes():
    assert is_decorative_remainder("")
    assert is_decorative_remainder(" (Official Audio)")
    assert is_decorative_remainder(" Official MV")
    # 이 함수는 장식만 판정한다. 현지화 별칭은 matcher가 문자 체계까지 확인한다.
    assert not is_decorative_remainder("(Through the Night)")
    assert not is_decorative_remainder(" (Part 2)")
    assert not is_decorative_remainder(" (Demo)")
    assert not is_decorative_remainder(" (Japanese Version)")
    assert not is_decorative_remainder(" Live")
    assert not is_decorative_remainder(" 어쿠스틱")
    assert not is_decorative_remainder(" 커버")


def test_version_markers_only_read_explicit_qualifier_contexts():
    assert version_markers("Live Forever") == set()
    assert version_markers("Oasis - Live Forever") == set()
    assert version_markers("Oasis - Live Forever (Live)") == {"live"}
    assert version_markers("Song - Acoustic Version") == {"acoustic"}
