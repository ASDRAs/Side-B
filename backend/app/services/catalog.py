import math
import re
from difflib import SequenceMatcher
from typing import Any

import httpx

from app.schemas.search import CandidateTrack


class CatalogClient:
    ITUNES_URL = "https://itunes.apple.com/search"
    DEEZER_URL = "https://api.deezer.com"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def search_tracks(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 10))
        payload = await self._itunes_search(query, safe_limit)
        return payload

    async def search_track_by_artist_title(self, artist: str, title: str) -> dict[str, Any] | None:
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


def _select_best_item(items: list[dict[str, Any]], title: str, artist: str) -> dict[str, Any] | None:
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


def _match_score(title: str, artist: str, expected_title: str, expected_artist: str) -> float:
    return (_ratio(title, expected_title) * 0.68) + (_ratio(artist, expected_artist) * 0.32)


def _ratio(left: str, right: str) -> float:
    left_norm = _compact(left)
    right_norm = _compact(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.9
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣ぁ-ゟ゠-ヿ一-鿿]+", "", value.lower())


def _upgrade_itunes_artwork(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/600x600bb.\1", url)


def deezer_rank_to_popularity(rank: int | None) -> int:
    if not rank or rank <= 0:
        return 0
    return max(0, min(100, int(math.log10(rank + 1) * 10)))
