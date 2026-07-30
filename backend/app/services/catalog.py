import re
from typing import Any

import httpx

from app.utils.text import text_ratio


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
    ) -> dict[str, Any] | None:
        term = f"{track_name} {artist}".strip()
        if not term:
            return None
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
            score = _catalog_match_score(title, item_artist, track_name, artist)
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
    cleaned = re.sub(
        r"\s+-\s+(remaster(?:ed)?|live|radio edit|single version).*$",
        " ",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _catalog_match_score(
    title: str, artist: str, expected_title: str, expected_artist: str = ""
) -> float:
    title_score = text_ratio(_clean_title(title), _clean_title(expected_title))
    artist_score = text_ratio(artist, expected_artist) if expected_artist else 1.0
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
        item_artist = str((item.get("artist") or {}).get("name") or "")
        if _looks_like_bad_version(title) or _looks_like_bad_version(item_artist):
            continue
        score = _catalog_match_score(title, item_artist, track_name, artist)
        artist_score = text_ratio(item_artist, artist) if artist else 1.0
        if artist and artist_score < 0.52:
            continue
        if best is None or score > best[0]:
            best = (score, item)
    if not best or best[0] < 0.72:
        return None
    return best[1]
