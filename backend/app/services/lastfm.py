from typing import Any

import httpx

from app.pipeline.errors import ServiceUnavailableError
from app.schemas.search import Tag


class LastFmClient:
    API_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str | None, http: httpx.AsyncClient) -> None:
        self.api_key = api_key
        self.http = http

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ServiceUnavailableError("Last.fm API key is not configured.")

        response = await self.http.get(
            self.API_URL,
            params={**params, "api_key": self.api_key, "format": "json"},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "error" in payload:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def tag_top_tracks(self, tag: str, limit: int = 5) -> list[dict[str, str]]:
        limit = max(1, min(limit, 10))
        payload = await self._get(
            {
                "method": "tag.gettoptracks",
                "tag": tag,
                "limit": limit,
            }
        )
        tracks = payload.get("tracks", {}).get("track", [])
        if isinstance(tracks, dict):
            tracks = [tracks]
        if not isinstance(tracks, list):
            return []

        normalized: list[dict[str, str]] = []
        for item in tracks[:limit]:
            title = str(item.get("name") or "")
            artist_data = item.get("artist") or {}
            artist = str(artist_data.get("name") or artist_data.get("#text") or "")
            if title and artist:
                normalized.append({"title": title, "artist": artist})
        return normalized

    async def track_top_tags(self, artist: str, title: str) -> list[Tag]:
        try:
            payload = await self._get(
                {
                    "method": "track.gettoptags",
                    "artist": artist,
                    "track": title,
                    "autocorrect": "1",
                }
            )
        except httpx.HTTPError:
            return []

        tags = payload.get("toptags", {}).get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        if not isinstance(tags, list):
            return []

        normalized: list[Tag] = []
        for item in tags:
            name = str(item.get("name") or "").strip()
            count = _parse_count(item.get("count"))
            if name:
                normalized.append(Tag(name=name, count=count))

        return sorted(normalized, key=lambda tag: tag.count, reverse=True)[:8]


def _parse_count(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0
