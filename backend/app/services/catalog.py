import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

import httpx

from app.utils.text import compact_text, text_ratio


class DeezerRateLimitError(Exception):
    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after


class ItunesRateLimitError(Exception):
    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after


class CatalogClient:
    ITUNES_URL = "https://itunes.apple.com/search"
    DEEZER_URL = "https://api.deezer.com"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def itunes_search_best(
        self,
        track_name: str,
        artist: str = "",
        limit: int = 5,
        min_score: float = 0.5,
        min_artist_score: float = 0.0,
        title_aliases: tuple[str, ...] = (),
        artist_aliases: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """min_artist_score는 총점과 별개로 요구하는 아티스트 일치 하한이다.

        총점만으로는 제목이 정확한 오답을 막을 수 없다. 가중치가 title 0.68 /
        artist 0.32라서 제목만 완전 일치해도 0.68이 나오기 때문이다. 하한은
        argmax 전에 적용해야 오답이 최고점을 차지하는 것을 막을 수 있다.

        검색어는 track_name/artist로 만들지만, 채점은 aliases 전체를 대상으로 한다.
        카탈로그가 제목과 아티스트를 다른 언어로 섞어 등록하기 때문이다.
        """
        term = f"{track_name} {artist}".strip()
        if not term:
            return None
        expected_titles = title_aliases or (track_name,)
        expected_artists = artist_aliases or ((artist,) if artist else ())
        try:
            response = await self.http.get(
                self.ITUNES_URL,
                params={
                    "term": term,
                    "entity": "song",
                    "limit": max(1, min(limit, 25)),
                },
                timeout=5.0,
            )
            if getattr(response, "status_code", 200) == 429:
                headers = getattr(response, "headers", {})
                raise ItunesRateLimitError(int(headers.get("Retry-After", 60)))
            response.raise_for_status()
            results = response.json().get("results", [])
        except ItunesRateLimitError:
            raise
        except Exception:
            return None
        best: tuple[float, dict[str, Any]] | None = None
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("trackName") or "")
            item_artist = str(item.get("artistName") or "")
            if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
                continue
            if (
                min_artist_score
                and _alias_artist_score(item_artist, expected_artists)
                < min_artist_score
            ):
                continue
            score = _alias_match_score(
                title, item_artist, expected_titles, expected_artists
            )
            if best is None or score > best[0]:
                best = (score, item)
        if not best or best[0] < min_score:
            return None
        return best[1]

    async def deezer_search_best(
        self,
        track_name: str,
        artist: str,
    ) -> dict[str, Any] | None:
        clean_name = _clean_title(track_name)
        queries = [
            f'track:"{clean_name}" artist:"{artist}"',
            f"{clean_name} {artist}".strip(),
            clean_name,
        ]
        for query in queries:
            try:
                response = await self.http.get(
                    f"{self.DEEZER_URL}/search",
                    params={"q": query},
                    timeout=8.0,
                )
                if response.status_code == 429:
                    raise DeezerRateLimitError(
                        int(response.headers.get("Retry-After", 60))
                    )
                items = response.json().get("data", [])
            except DeezerRateLimitError:
                raise
            except Exception:
                continue
            best = _select_deezer_item(items, clean_name, artist)
            if best:
                return best
        return None


_BAD_VERSION_MARKERS = (
    "karaoke",
    "instrumental",
    "instrumental karaoke",
    "inst.",
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


def _looks_like_bad_version(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _BAD_VERSION_MARKERS)


def _clean_title(value: str) -> str:
    cleaned = re.sub(r"\(.*?\)|\[.*?\]", " ", value)
    # "- 2012 Remaster"처럼 연도가 키워드 앞에 오는 표기도 벗긴다. 스트리밍
    # 카탈로그에서 흔한 형태인데 이게 남으면 원곡 요청과 아예 매칭되지 않는다
    # ("봄날 - 2017 Remaster" 대 "봄날" -> 0.00).
    cleaned = re.sub(
        r"\s+-\s+(?:\d{4}\s+)?(remaster(?:ed)?|live|radio edit|single version).*$",
        " ",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _catalog_match_score(
    title: str, artist: str, expected_title: str, expected_artist: str = ""
) -> float:
    return _alias_match_score(
        title,
        artist,
        (expected_title,),
        (expected_artist,) if expected_artist else (),
    )


def _alias_match_score(
    title: str,
    artist: str,
    expected_titles: Sequence[str],
    expected_artists: Sequence[str],
) -> float:
    """제목과 아티스트를 각각 모든 표기 중 최고점으로 채점한다.

    카탈로그는 표기 언어를 섞어 등록한다. iTunes의 IU "너랑 나"는 제목이 한국어,
    아티스트가 영문인 "너랑 나 (YOU&I)" / "IU"다. (제목, 아티스트) 쌍 안에서만
    비교하면 한국어 쌍은 아티스트에서, 영문 쌍은 제목에서 탈락해 어느 쪽으로도
    확정되지 않는다.
    """
    title_score = max(
        (
            text_ratio(_clean_title(title), _clean_title(expected))
            for expected in expected_titles
            if expected
        ),
        default=0.0,
    )
    artist_score = _alias_artist_score(artist, expected_artists)
    return (title_score * 0.68) + (artist_score * 0.32)


# 협업·병기 표기를 쪼개는 구분자. "IU & G-DRAGON"에서 "IU"를 꺼내기 위한 것이다.
_ARTIST_SEPARATORS = re.compile(
    r"\s*(?:&|,|/)\s*|\s+(?:feat(?:uring)?|ft|with)\.?\s*|[()\[\]]",
    re.IGNORECASE,
)


def _alias_artist_score(artist: str, expected_artists: Sequence[str]) -> float:
    # 비교에 쓸 수 없는 표기(공백·문장부호뿐)는 "모르는 것"으로 본다.
    # 여기서 걸러내지 않고 _artist_ratio가 만점을 주면, max() 안에서 그 한 개가
    # 정상 alias의 판정을 덮어써 아티스트 게이트 전체가 무력해진다.
    targets = [expected for expected in expected_artists if compact_text(expected)]
    if not targets:
        return 1.0  # 기대 아티스트를 모르면 제목만으로 판단한다.
    return max((_artist_ratio(artist, expected) for expected in targets), default=0.0)


def _artist_ratio(artist: str, expected: str) -> float:
    """아티스트 전용 비교. text_ratio와 달리 부분문자열에 점수를 주지 않는다.

    text_ratio는 한쪽이 다른 쪽에 포함되기만 하면 0.9를 준다. 제목에는 맞는
    규칙이지만("Creep"은 "Creep (Acoustic)"의 일부다) 아티스트에는 해롭다.
    "Adele"이 "Definitely Not Adele"에 포함된다고 같은 가수가 되지는 않는다.

    대신 구분자로 쪼갠 조각이 정확히 일치하면 만점을 준다. 협업 표기
    "IU & G-DRAGON"과 병기 표기 "IU (아이유)"를 살리면서, 구분자가 없는
    "Definitely Not Adele"은 통과시키지 않는다.
    """
    target = compact_text(expected)
    if not target:
        # 호출부(_alias_artist_score)가 걸러야 하는 입력이다. 여기까지 왔다면
        # 검증할 수 없다는 뜻이므로 만점이 아니라 0점을 준다.
        return 0.0
    parts = {compact_text(part) for part in _ARTIST_SEPARATORS.split(artist or "")}
    if target in parts - {""}:
        return 1.0
    candidate = compact_text(artist or "")
    if not candidate:
        return 0.0
    return SequenceMatcher(None, candidate, target).ratio()


def _strict_title_ratio(title: str, expected: str) -> float:
    """Deezer metadata 후보용 제목 비교. 짧은 부분문자열 특례는 허용하지 않는다."""
    candidate = compact_text(_clean_title(title))
    target = compact_text(_clean_title(expected))
    if not candidate or not target:
        return 0.0
    if candidate == target:
        return 1.0
    if min(len(candidate), len(target)) <= 4:
        return 0.0
    return SequenceMatcher(None, candidate, target).ratio()


def _select_deezer_item(
    items: Any, track_name: str, artist: str
) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        artist_payload = item.get("artist")
        item_artist = str(
            artist_payload.get("name")
            if isinstance(artist_payload, dict) and artist_payload.get("name")
            else ""
        )
        if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
            continue
        if _strict_title_ratio(title, track_name) < 0.8:
            continue
        score = _catalog_match_score(title, item_artist, track_name, artist)
        artist_score = _alias_artist_score(item_artist, (artist,))
        if artist and artist_score < 0.8:
            continue
        if best is None or score > best[0]:
            best = (score, item)
    if not best or best[0] < 0.72:
        return None
    return best[1]
