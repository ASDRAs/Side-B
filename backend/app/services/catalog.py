import math
import re
from typing import Any

import httpx

from app.schemas.search import CandidateTrack
from app.utils.text import text_ratio


class DeezerRateLimitError(Exception):
    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after


class CatalogClient:
    ITUNES_URL = "https://itunes.apple.com/search"
    DEEZER_URL = "https://api.deezer.com"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def search_tracks(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 10))
        payload = await self._itunes_search(query, safe_limit)
        return payload

    async def search_track_by_artist_title(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        items = await self.search_tracks(f"{title} {artist}", limit=5)
        return _select_best_item(items, title, artist)

    @staticmethod
    def normalize_track(track: dict[str, Any]) -> CandidateTrack | None:
        title = str(track.get("trackName") or "").strip()
        artist = str(track.get("artistName") or "").strip()
        track_id = str(track.get("trackId") or "").strip()
        album_art = _upgrade_itunes_artwork(str(track.get("artworkUrl100") or ""))

        if not title or not artist or not track_id or not album_art:
            return None

        return CandidateTrack(
            providerId=f"itunes:{track_id}",
            artist=artist,
            title=title,
            albumArt=album_art,
            popularity=50,
            tags=[],
        )

    async def _itunes_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        response = await self.http.get(
            self.ITUNES_URL,
            params={"term": query, "entity": "song", "limit": limit},
        )
        response.raise_for_status()
        items = response.json().get("results", [])
        return items if isinstance(items, list) else []

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
            response.raise_for_status()
            results = response.json().get("results", [])
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


def _select_best_item(
    items: list[dict[str, Any]], title: str, artist: str
) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for item in items:
        score = _match_score(
            str(item.get("trackName") or ""),
            str(item.get("artistName") or ""),
            title,
            artist,
        )
        if best is None or score > best[0]:
            best = (score, item)
    if not best or best[0] < 0.42:
        return None
    return best[1]


def _match_score(
    title: str, artist: str, expected_title: str, expected_artist: str
) -> float:
    return (text_ratio(title, expected_title) * 0.68) + (
        text_ratio(artist, expected_artist) * 0.32
    )


def _upgrade_itunes_artwork(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/600x600bb.\1", url)


def deezer_rank_to_popularity(rank: int | None) -> int:
    if not rank or rank <= 0:
        return 0
    return max(0, min(100, int(math.log10(rank + 1) * 10)))


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
