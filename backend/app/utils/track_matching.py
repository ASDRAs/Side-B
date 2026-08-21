import re
from collections.abc import Sequence
from difflib import SequenceMatcher

from app.utils.text import compact_text

BAD_VERSION_MARKERS = (
    "karaoke",
    "instrumental",
    "instrumental karaoke",
    "inst.",
    "MR",
    "originally performed",
    "originally perfomed",
    "tribute",
    "cover",
    "cover version",
    "sped up",
    "slowed",
    "nightcore",
    "musicmaru",
    "뮤직마루",
    "노래방",
    "반주",
)

VERSION_MARKERS = ("live", "remix", "acoustic")

_VERSION_SUFFIX_PATTERN = re.compile(
    r"\s[-\u2013\u2014|:]\s*"
    r"((?:live|remix|acoustic)(?:\s+(?:version|ver\.?))?)\s*$",
    re.IGNORECASE,
)

_MR_VERSION_PATTERN = re.compile(
    r"""
    (?:
        [\[(]\s*MR
        (?:\s*(?:[/|,:-]\s*[^\])]+|ver(?:sion)?\.?))?
        \s*[\])]
        |
        (?:\s|[-\u2013\u2014|])MR(?:\s+ver(?:sion)?\.?)?\s*$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BRACKET_CONTENT_PATTERN = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_DECORATIVE_QUALIFIER_PATTERN = re.compile(
    r"^(?:"
    r"official(?:\s+music)?\s+(?:video|audio)|official\s+(?:mv|m/v)|"
    r"audio|video|(?:lyric|lyrics)\s+video|visuali[sz]er|"
    r"(?:feat(?:uring)?|ft|with|prod)\.?\s+.+|"
    r"(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?"
    r")$",
    re.IGNORECASE,
)
_VERSION_QUALIFIER_PATTERN = re.compile(
    r"^(?:live|remix|acoustic)(?:\s+(?:version|ver\.?))?$",
    re.IGNORECASE,
)
_IDENTITY_QUALIFIER_PATTERN = re.compile(
    r"^(?:"
    r"(?:part|pt|chapter|episode|act|disc|cd)\.?\s*[\w-]+|"
    r".+\s+(?:version|ver\.?)|"
    r"reprise|demo(?:\s+\d+)?|take\s+\d+"
    r")$",
    re.IGNORECASE,
)
_UNDELIMITED_SUFFIX_MARKERS = {
    "instrumental",
    "nightcore",
    "sped up",
    "slowed",
    "karaoke",
    "노래방",
    "반주",
}


def _contains_text_marker(value: str, marker: str) -> bool:
    return bool(
        re.search(
            rf"(?<!\w){re.escape(marker)}(?!\w)",
            value,
            re.IGNORECASE,
        )
    )


def _bracket_contents(value: str) -> list[str]:
    return [
        content.strip()
        for match in _BRACKET_CONTENT_PATTERN.finditer(value)
        for content in match.groups()
        if content and content.strip()
    ]


def contains_bad_version_marker(
    value: str,
    marker: str,
    *,
    title_context: bool = True,
) -> bool:
    if marker == "MR":
        return bool(_MR_VERSION_PATTERN.search(value))
    if not title_context:
        return _contains_text_marker(value, marker)
    if any(
        _contains_text_marker(content, marker) for content in _bracket_contents(value)
    ):
        return True
    escaped = re.escape(marker)
    if re.search(
        rf"(?:\s[-\u2013\u2014|:]\s*){escaped}"
        rf"(?:\s+(?:version|ver\.?))?\s*$",
        value,
        re.IGNORECASE,
    ):
        return True
    if re.search(
        rf"(?<!\w){escaped}(?!\w)\s+(?:version|ver\.?)\s*$",
        value,
        re.IGNORECASE,
    ):
        return True
    return marker.casefold() in _UNDELIMITED_SUFFIX_MARKERS and bool(
        re.search(rf"(?<!\w){escaped}(?!\w)\s*$", value, re.IGNORECASE)
    )


def looks_like_bad_version(value: str, *, title_context: bool = True) -> bool:
    return any(
        contains_bad_version_marker(value, marker, title_context=title_context)
        for marker in BAD_VERSION_MARKERS
    )


def identity_qualifiers(value: str) -> tuple[str, ...]:
    qualifiers: list[str] = []
    for content in _bracket_contents(value):
        if _DECORATIVE_QUALIFIER_PATTERN.fullmatch(content):
            continue
        if _VERSION_QUALIFIER_PATTERN.fullmatch(content):
            continue
        if looks_like_bad_version(f"({content})"):
            continue
        if not _IDENTITY_QUALIFIER_PATTERN.fullmatch(content):
            continue
        normalized = compact_text(content)
        if normalized:
            qualifiers.append(normalized)
    return tuple(qualifiers)


def identity_qualifiers_match(candidate: str, expected: str) -> bool:
    return identity_qualifiers(candidate) == identity_qualifiers(expected)


def clean_title(value: str) -> str:
    cleaned = re.sub(r"\(.*?\)|\[.*?\]", " ", value)
    # "- 2012 Remaster"처럼 연도가 키워드 앞에 오는 표기도 벗긴다. 이게 남으면
    # 원곡 요청과 매칭되지 않는다("봄날 - 2017 Remaster" 대 "봄날" -> 0.00).
    cleaned = re.sub(
        r"\s+-\s+(?:\d{4}\s+)?(remaster(?:ed)?|live|radio edit|single version).*$",
        " ",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


# 협업·병기 표기를 쪼개는 구분자. "IU & G-DRAGON"에서 "IU"를 꺼낸다.
_ARTIST_SEPARATORS = re.compile(
    r"\s*(?:&|,|/)\s*|\s+(?:feat(?:uring)?|ft|with)\.?\s*|[()\[\]]",
    re.IGNORECASE,
)


def _artist_pieces(value: str) -> list[str]:
    return [
        piece.strip()
        for piece in _ARTIST_SEPARATORS.split(value or "")
        if piece and piece.strip() and compact_text(piece)
    ]


def _artist_parts(value: str) -> set[str]:
    return {compact_text(piece) for piece in _artist_pieces(value)} - {""}


# 제목이 참여자를 밝히는 형식. `with`는 괄호 안에서만 크레딧으로 읽어
# `Boy With Luv`의 `Luv` 같은 일반 제목 조각을 참여자로 오인하지 않는다.
_CREDIT_CLAUSE = re.compile(
    r"\b(?:feat(?:uring)?|ft|prod)\b\.?\s*(?P<names>[^()\[\]]*)"
    r"|[(\[]\s*with\s+(?P<with_names>[^()\[\]]*)[)\]]",
    re.IGNORECASE,
)


def _credit_clause(title: str) -> str:
    return " ".join(
        text
        for match in _CREDIT_CLAUSE.finditer(title or "")
        for text in (match.group("names"), match.group("with_names"))
        if text
    )


def credits_are_accounted_for(missing: list[str], credit_text: str) -> bool:
    """후보가 빠뜨린 참여자를 후보 제목의 명시적 크레딧이 밝히는지 본다.

    진짜 협업이면 카탈로그가 아티스트란에서 뺀 참여자를 제목에 남기는 경우가
    있다. 제목 아무 위치의 이름을 근거로 삼으면 `Earth, Wind & Fire` 요청에
    `Wind and Fire`라는 다른 곡이 통과하므로 feat./ft./prod./(with ...) 절 안만
    검사한다. 짧은 이름이 일반 단어에 포함되는 오탐을 막기 위해 단어 경계도
    요구한다(`IU`는 `Genius`의 부분문자열이다).
    """
    clause = _credit_clause(credit_text)
    if not clause or not missing:
        return False
    return all(
        re.search(rf"\b{re.escape(piece)}\b", clause, re.IGNORECASE)
        for piece in missing
    )


def artist_ratio(artist: str, expected: str, credit_text: str = "") -> float:
    """아티스트 전용 비교. 제목용 부분문자열 shortcut을 사용하지 않는다.

    구분자로 쪼갠 조각이 정확히 일치하면 협업 표기 `IU & G-DRAGON`과 병기 표기
    `IU (아이유)`를 살릴 수 있다. 요청 쪽도 공급자별 협업 크레딧 차이를 위해
    쪼개지만, 그룹명도 같은 구분자를 사용하므로 무조건 허용하지 않는다.
    후보가 빠뜨린 요청 참여자가 후보 제목의 명시적 크레딧에 남아 있을 때만
    협업으로 인정한다. 근거가 없으면 `Earth, Wind & Fire` 요청에 `Earth`가,
    `AC/DC` 요청에 `AC`가 통과하는 오탐이 생긴다.

    반대 방향인 후보가 그룹이고 요청이 그 조각인 경우는 기존 동작을 유지한다.
    `Simon & Garfunkel`에 `Simon`이 붙는 알려진 약점이다.
    """
    target_parts = _artist_parts(expected)
    if not target_parts:
        return 0.0
    candidate_parts = _artist_parts(artist)
    if candidate_parts & target_parts:
        missing = [
            piece
            for piece in _artist_pieces(expected)
            if compact_text(piece) not in candidate_parts
        ]
        if not missing or credits_are_accounted_for(missing, credit_text):
            return 1.0
    candidate = compact_text(artist or "")
    if not candidate:
        return 0.0
    return SequenceMatcher(None, candidate, compact_text(expected)).ratio()


def artist_score(
    artist: str,
    expected_artists: Sequence[str],
    credit_text: str = "",
) -> float:
    """알려진 표기 중 최고 아티스트 점수를 반환한다.

    공백·문장부호뿐인 표기는 비교 대상에서 제외한다. 그렇지 않으면 max() 안의
    비교 불가능한 alias 하나가 만점을 받아 정상 alias의 판정을 덮을 수 있다.
    기대 아티스트가 하나도 없을 때만 제목 단독 판정을 위해 1.0을 반환한다.
    """
    raw_targets = [
        expected.strip() for expected in expected_artists if expected.strip()
    ]
    targets = [expected for expected in raw_targets if compact_text(expected)]
    if not raw_targets:
        return 1.0
    if not targets:
        candidate = artist.strip().casefold()
        return (
            1.0 if candidate in {target.casefold() for target in raw_targets} else 0.0
        )
    return max(
        (artist_ratio(artist, expected, credit_text) for expected in targets),
        default=0.0,
    )


def strict_title_ratio(title: str, expected: str) -> float:
    """짧은 제목의 부분 일치를 허용하지 않는 카탈로그 제목 비교."""
    if not identity_qualifiers_match(title, expected):
        return 0.0
    candidate = compact_text(clean_title(title))
    target = compact_text(clean_title(expected))
    if not candidate or not target:
        return 0.0
    if candidate == target:
        return 1.0
    if min(len(candidate), len(target)) <= 4:
        return 0.0
    return SequenceMatcher(None, candidate, target).ratio()


def version_markers(value: str) -> set[str]:
    contexts = _bracket_contents(value)
    suffix = _VERSION_SUFFIX_PATTERN.search(value)
    if suffix:
        contexts.append(suffix.group(1))
    return {
        marker
        for marker in VERSION_MARKERS
        if any(_contains_text_marker(context, marker) for context in contexts)
    }
