from app.utils.track_matching import (
    artist_ratio,
    artist_score,
    clean_title,
    identity_qualifiers_match,
    looks_like_bad_version,
    strict_title_ratio,
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
