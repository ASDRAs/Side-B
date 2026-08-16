import pytest

from app.services.catalog import (
    _alias_artist_score,
    _credits_are_accounted_for,
    _select_deezer_item,
    _strict_title_ratio,
)


def _item(track_id, title, artist):
    return {
        "id": track_id,
        "title": title,
        "artist": {"name": artist},
    }


def test_artist_score_allows_explicit_collaboration_credit():
    assert _alias_artist_score("IU & SUGA", ("IU",)) == 1.0
    assert _alias_artist_score("IU (아이유)", ("IU",)) == 1.0


def test_artist_score_does_not_treat_arbitrary_substring_as_same_artist():
    assert _alias_artist_score("Definitely Not Adele", ("Adele",)) < 0.8
    assert _alias_artist_score("TAEMIN", ("TAEYEON",)) < 0.8
    assert _alias_artist_score("Within Temptation", ("Temptation",)) < 0.8
    assert _alias_artist_score("Feather", ("her",)) < 0.8


def test_deezer_metadata_rejects_wrong_artist_even_when_title_is_exact():
    items = [_item(1, "Hello", "Adele")]

    assert _select_deezer_item(items, "Hello", "Definitely Not Adele") is None


def test_deezer_metadata_rejects_short_partial_title_from_same_artist():
    items = [_item(1, "I", "TAEYEON"), _item(2, "Once", "U2")]

    assert _select_deezer_item(items, "If", "TAEYEON") is None
    assert _select_deezer_item(items, "One", "U2") is None


def test_deezer_metadata_accepts_matching_collaboration():
    items = [_item(1, "eight", "IU & SUGA")]

    assert _select_deezer_item(items, "eight", "IU") == items[0]


def test_unusable_artist_alias_does_not_override_a_real_one():
    """비교 불가한 표기가 섞여도 정상 alias의 판정을 덮으면 안 된다.

    max()로 여러 표기 중 최고점을 쓰므로, 그중 하나가 만점을 받으면 게이트가
    통째로 열린다. LLM이 주는 artist_name에는 값 검증이 없어 도달 가능하다.
    """
    assert _alias_artist_score("Flow Music", ("IU",)) < 0.8
    assert _alias_artist_score("Flow Music", ("IU", "!!!")) < 0.8
    assert _alias_artist_score("Flow Music", ("!!!", "IU")) < 0.8

    items = [_item(1, "Hello", "Adele")]
    assert _select_deezer_item(items, "Hello", "IU") is None
    assert _select_deezer_item(items, "Hello", "IU & !!!") is None


def test_unusable_artist_is_treated_as_unknown_not_as_a_match():
    """비교 불가한 표기만 있으면 아티스트를 모르는 것과 같게 다룬다.

    게이트를 통과시키는 것이 아니라 걸지 않는 것이므로, 판정은 제목 단독으로
    떨어진다. artist=""로 호출하는 기존 경로와 동작이 같아야 한다.
    """
    assert _alias_artist_score("Adele", ()) == 1.0
    assert _alias_artist_score("Adele", ("",)) == 1.0
    assert _alias_artist_score("Adele", ("!!!",)) == _alias_artist_score("Adele", ())


# 대소문자·공백·문장부호만 다른 표기는 같은 아티스트로 봐야 한다.
SPELLING_VARIANTS = [
    pytest.param("iu", "IU", id="case"),
    pytest.param("BlackPink", "BLACKPINK", id="camel-case"),
    pytest.param("BLACK PINK", "BLACKPINK", id="space"),
    pytest.param("fromis 9", "fromis_9", id="underscore"),
    pytest.param("G DRAGON", "G-DRAGON", id="hyphen"),
    pytest.param("Panic! At The Disco", "Panic At The Disco", id="exclamation"),
    pytest.param("Tyler, The Creator", "Tyler The Creator", id="comma-in-name"),
]


@pytest.mark.parametrize("catalog_artist,expected", SPELLING_VARIANTS)
def test_artist_score_normalizes_spelling_variants(catalog_artist, expected):
    assert _alias_artist_score(catalog_artist, (expected,)) == 1.0


# 참여자가 몇 번째에 오든 잡혀야 한다.
COLLABORATION_POSITIONS = [
    pytest.param("Zion.T, IU, Crush", "Zion.T", id="first"),
    pytest.param("Zion.T, IU, Crush", "IU", id="middle"),
    pytest.param("Zion.T, IU, Crush", "Crush", id="last"),
    pytest.param("IU & SUGA", "SUGA", id="ampersand-second"),
    pytest.param("Epik High feat. IU", "IU", id="feat"),
]


@pytest.mark.parametrize("catalog_artist,expected", COLLABORATION_POSITIONS)
def test_artist_score_finds_credit_in_any_position(catalog_artist, expected):
    assert _alias_artist_score(catalog_artist, (expected,)) == 1.0


# 요청 쪽이 합동 표기이고 카탈로그가 단독 표기인 경우. Last.fm은 협업곡을
# "BTS, Halsey"로 싣지만 iTunes는 "BTS"로 싣는다. 빠진 참여자는 제목에 남는다.
REQUESTED_COLLABORATIONS = [
    pytest.param("BTS", "BTS, Halsey", "Boy With Luv (feat. Halsey)", id="comma"),
    pytest.param("Halsey", "BTS, Halsey", "Boy With Luv (feat. BTS)", id="comma-second"),
    pytest.param("IU", "IU & SUGA", "eight (Prod.&Feat. SUGA)", id="ampersand"),
    pytest.param("Epik High", "Epik High feat. IU", "Love Story (feat. IU)", id="feat"),
]


@pytest.mark.parametrize(
    "catalog_artist,requested,candidate_title", REQUESTED_COLLABORATIONS
)
def test_artist_score_matches_when_request_lists_more_artists(
    catalog_artist, requested, candidate_title
):
    """실측 회귀: `Boy With Luv (feat. Halsey)`가 여기서 탈락했다.

    제목 일치도가 1.000인데 아티스트가 0.500이라 하한 0.8에 걸렸다. 요청 쪽을
    통째로만 비교하면 합동 표기에서 정상 곡을 잃는다.
    """
    assert _alias_artist_score(catalog_artist, (requested,), candidate_title) == 1.0


# 그룹명도 협업과 같은 구분자를 쓴다. 근거 없이 요청 쪽을 쪼개면 다른 아티스트가
# 통과한다.
GROUP_NAME_IMPOSTORS = [
    pytest.param("Earth", "Earth, Wind & Fire", "September", id="comma-group"),
    pytest.param("Simon", "Simon & Garfunkel", "The Sound of Silence", id="duo"),
    pytest.param("AC", "AC/DC", "Back In Black", id="slash"),
    pytest.param("Tyler", "Tyler, The Creator", "Yonkers", id="comma-in-name"),
]


@pytest.mark.parametrize(
    "catalog_artist,requested,candidate_title", GROUP_NAME_IMPOSTORS
)
def test_artist_score_rejects_a_group_name_split_into_pieces(
    catalog_artist, requested, candidate_title
):
    """빠진 조각이 후보 제목에 없으면 협업이라고 볼 근거가 없다."""
    assert _alias_artist_score(catalog_artist, (requested,), candidate_title) < 0.8


def test_credit_evidence_is_required_not_optional():
    """제목을 넘기지 않으면 요청 쪽 분해는 인정되지 않는다.

    호출부가 제목을 빠뜨렸을 때 게이트가 조용히 넓어지면 안 된다. 근거가 없으면
    변경 전과 같은 판정으로 돌아간다.
    """
    assert _alias_artist_score("BTS", ("BTS, Halsey",)) < 0.8
    assert _alias_artist_score("BTS", ("BTS, Halsey",), "Spring Day") < 0.8


def test_splitting_the_request_does_not_open_the_gate():
    """근거가 있어도 남남은 계속 탈락해야 한다."""
    assert _alias_artist_score("Adele", ("Definitely Not Adele",)) < 0.8
    assert _alias_artist_score("TAEMIN", ("TAEYEON, IU",), "If (feat. IU)") < 0.8
    assert _alias_artist_score("Flow Music", ("IU, 아이유",), "밤편지") < 0.8


def test_missing_credit_needs_a_word_boundary_not_a_substring():
    """짧은 이름은 아무 제목에나 들어 있다. `IU`는 `Genius` 안에 있다.

    점수까지 보면 `SUGA` 대 `SUGA, IU`는 퍼지 비교만으로도 0.8이라(이 변경과
    무관한 기존 동작) 근거 규칙만 따로 확인한다.
    """
    assert not _credits_are_accounted_for(["IU"], "Genius")
    assert _credits_are_accounted_for(["IU"], "Love Story (feat. IU)")
    assert not _credits_are_accounted_for(["Fire"], "Firestarter")
    assert not _credits_are_accounted_for(["Halsey"], "")


def test_artist_score_does_not_bridge_languages_on_its_own():
    """언어가 다른 같은 아티스트는 이 함수가 잇지 못한다. 호출부 계약이다.

    카탈로그마다 표기 언어가 달라(Last.fm 태연 / Deezer TAEYEON) 한쪽만 넘기면
    정상 곡을 놓친다. resolver가 원표기와 대체표기를 모두 넘기는 이유이며,
    실제로 이 누락 때문에 커버리지 측정이 59%를 51%로 잘못 셌던 적이 있다.
    """
    assert _alias_artist_score("TAEYEON", ("태연",)) == 0.0
    assert _alias_artist_score("AKMU", ("악동뮤지션",)) == 0.0

    # 두 표기를 함께 주는 것이 정상 사용법이다.
    assert _alias_artist_score("TAEYEON", ("태연", "TAEYEON")) == 1.0
    assert _alias_artist_score("AKMU", ("악동뮤지션", "AKMU")) == 1.0


def test_artist_score_known_limitation_group_name_prefix():
    """알려진 약점: 구분자 앞 조각이 그대로 일치하면 통과한다.

    협업 표기를 살리려고 구분자로 쪼개는 대가다. 듀오·그룹명이 구분자를 품고
    있으면 그 앞부분만으로도 만점이 된다. 기대 아티스트가 iTunes로 확정한
    정식명이라 실제로는 잘 안 걸리지만, 동작을 고정해 두고 바꿀 때 의식하게
    한다.
    """
    assert _alias_artist_score("Simon & Garfunkel", ("Simon",)) == 1.0
    assert _alias_artist_score("Above & Beyond", ("Above",)) == 1.0


def test_strict_title_ratio_short_title_needs_exact_match():
    """4자 이하 제목은 정확히 같지 않으면 0. If -> I 오선택을 막는다."""
    assert _strict_title_ratio("I", "I") == 1.0
    assert _strict_title_ratio("I", "If") == 0.0
    assert _strict_title_ratio("One", "Once") == 0.0
    assert _strict_title_ratio("봄날", "봄") == 0.0


def test_strict_title_ratio_keeps_normalization_and_tolerance():
    assert _strict_title_ratio("Lilac", "LILAC") == 1.0
    assert _strict_title_ratio("Ditto [Remix]", "Ditto") == 1.0
    assert _strict_title_ratio("", "") == 0.0
    # 5자 이상은 퍼지 허용이라 근접 오답이 통과할 수 있다(게이트 0.8 기준).
    assert _strict_title_ratio("Creep", "Creeps") >= 0.8


# 버전 구분은 이 함수가 하지 않는다. preview._version_markers가 담당한다.
VERSION_SUFFIXES = [
    "Creep (Acoustic)",
    "Creep [Live]",
    "Creep - Remaster",
    "Creep - Remastered",
    "Creep - Remastered 2012",
    "Creep - 2012 Remaster",
    "Creep - Live",
    "Creep - Radio Edit",
]


@pytest.mark.parametrize("titled", VERSION_SUFFIXES)
def test_strict_title_ratio_ignores_version_suffix(titled):
    """연도가 키워드 앞에 오는 '- 2012 Remaster'도 벗겨야 한다.

    이 형태가 남으면 원곡 요청과 아예 매칭되지 않는다. 한글 제목에서는
    공통 문자가 없어 '봄날 - 2017 Remaster' 대 '봄날'이 0.00까지 떨어졌다.
    """
    assert _strict_title_ratio(titled, "Creep") == 1.0


def test_strict_title_ratio_handles_non_latin_version_suffix():
    assert _strict_title_ratio("봄날 - 2017 Remaster", "봄날") == 1.0


MALFORMED_PAYLOADS = [
    pytest.param("not-a-list", id="not-a-list"),
    pytest.param([], id="empty"),
    pytest.param([None, "x"], id="non-dict-items"),
    pytest.param([{"id": 1, "title": "Hello"}], id="missing-artist"),
    pytest.param([{"id": 1, "title": "Hello", "artist": "Adele"}], id="artist-is-str"),
    pytest.param([{"id": 1, "title": "Hello", "artist": {}}], id="artist-empty-dict"),
]


@pytest.mark.parametrize("items", MALFORMED_PAYLOADS)
def test_deezer_selector_survives_malformed_payload(items):
    assert _select_deezer_item(items, "Hello", "Adele") is None
