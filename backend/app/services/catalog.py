from collections.abc import Sequence
from typing import Any

import httpx

from app.utils import track_matching
from app.utils.text import text_ratio

_alias_artist_score = track_matching.artist_score
_clean_title = track_matching.clean_title
_identity_qualifiers_match = track_matching.identity_qualifiers_match
_looks_like_bad_version = track_matching.looks_like_bad_version
_strict_title_ratio = track_matching.strict_title_ratio


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
            if _looks_like_bad_version(title) or _looks_like_bad_version(
                item_artist, title_context=False
            ):
                continue
            if (
                min_artist_score
                and _alias_artist_score(item_artist, expected_artists, title)
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
            if _identity_qualifiers_match(title, expected)
            else 0.0
            for expected in expected_titles
            if expected
        ),
        default=0.0,
    )
    artist_score = _alias_artist_score(artist, expected_artists, title)
    return (title_score * 0.68) + (artist_score * 0.32)


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
        if _looks_like_bad_version(title) or _looks_like_bad_version(
            item_artist, title_context=False
        ):
            continue
        if _strict_title_ratio(title, track_name) < 0.8:
            continue
        score = _catalog_match_score(title, item_artist, track_name, artist)
        artist_score = _alias_artist_score(item_artist, (artist,), title)
        if artist and artist_score < 0.8:
            continue
        if best is None or score > best[0]:
            best = (score, item)
    if not best or best[0] < 0.72:
        return None
    return best[1]
