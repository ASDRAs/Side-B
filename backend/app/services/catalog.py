import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.utils import track_matching
from app.utils.text import text_ratio

logger = logging.getLogger(__name__)

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


# Deezer는 쿼터 초과도 HTTP 200 + error 본문으로 알린다. 미수록(DataException
# 800 등)과 달리 일시 제한이라 빈 결과로 보면 안 된다.
DEEZER_QUOTA_ERROR_CODE = 4


def retry_after_seconds(value: str | None, default: int) -> int:
    """Retry-After를 초로 읽는다. 날짜·음수·제어 문자가 섞인 값은 기본값으로 둔다."""
    candidate = str(value or "").strip()
    return int(candidate) if candidate.isdecimal() else default


class ProviderGate:
    """한 공급자에 나가는 API 호출의 동시 실행 상한과 회로차단기.

    공급자마다 모듈 전역 인스턴스 하나를 추천·미리 듣기·장르 분석이 함께 쓴다.
    CatalogClient는 호출마다 새로 만들어지므로 상태를 인스턴스에 두면 한 호출이
    받은 429를 다음 호출이 모른다.
    """

    def __init__(
        self,
        name: str,
        error: type[ItunesRateLimitError] | type[DeezerRateLimitError],
        concurrency: int | None,
    ) -> None:
        self._name = name
        self._error = error
        self._concurrency = concurrency
        self.reset()

    def reset(self) -> None:
        """차단기를 닫고 자리를 새로 만든다.

        세마포어는 처음 대기가 생긴 이벤트 루프에 묶인다. 테스트처럼 루프가
        바뀌면 새로 만들어야 한다.
        """
        self._limited_until = 0.0
        self._semaphore = (
            asyncio.Semaphore(self._concurrency) if self._concurrency else None
        )

    def is_limited(self) -> bool:
        return time.monotonic() < self._limited_until

    def mark_limited(self, retry_after: int) -> None:
        self._limited_until = max(self._limited_until, time.monotonic() + retry_after)
        logger.warning("[%s] 호출 제한 — %d초 차단", self._name, retry_after)

    def _raise_if_limited(self) -> None:
        if self.is_limited():
            remaining = math.ceil(self._limited_until - time.monotonic())
            raise self._error(max(1, remaining))

    async def get(self, http: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
        """자리를 얻어 GET을 보낸다. 429는 차단기에 기록하고 예외로 올린다.

        차단기는 자리를 얻은 뒤, 호출 직전에 다시 확인한다. 기다리는 동안 다른
        호출이 429를 받아 차단기를 열 수 있기 때문이다. 429 기록도 자리를 놓기
        전에 끝내야 깨어난 호출이 열린 차단기를 본다. 이미 나간 호출은 되돌릴 수
        없으므로, 보장하는 것은 기록 이후 새 호출이 나가지 않는다는 것이다.
        """
        self._raise_if_limited()
        async with self._semaphore or contextlib.nullcontext():
            self._raise_if_limited()
            response = await http.get(url, **kwargs)
            if getattr(response, "status_code", 200) == 429:
                headers = getattr(response, "headers", None) or {}
                retry_after = retry_after_seconds(headers.get("Retry-After"), 60)
                self.mark_limited(retry_after)
                raise self._error(retry_after)
            return response


# 동시 8은 추천 경로가 쓰던 값이다. 미리 듣기·장르 분석도 같은 자리를 나눈다.
ITUNES = ProviderGate("iTunes", ItunesRateLimitError, concurrency=8)
# ponytail: Deezer에는 원래 동시 실행 상한이 없어 차단기만 공유한다. 상한을
# 두면 쿼터 본문 확인도 자리 안으로 옮겨야 기록이 자리 반납보다 먼저 끝난다.
DEEZER = ProviderGate("Deezer", DeezerRateLimitError, concurrency=None)


def raise_if_deezer_quota_error(payload: object) -> None:
    """쿼터 초과 본문이면 Deezer 차단기를 열고 예외로 올린다.

    그대로 두면 `data`가 없어 빈 결과로 보이고, 남은 검색어를 계속 보내며,
    결과 캐시에 미수록으로 남는다.
    """
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if isinstance(error, dict) and error.get("code") == DEEZER_QUOTA_ERROR_CODE:
        logger.warning("[Deezer] 쿼터 초과: %s", error.get("message"))
        DEEZER.mark_limited(60)
        raise DeezerRateLimitError(60)


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
            response = await ITUNES.get(
                self.http,
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
                response = await DEEZER.get(
                    self.http,
                    f"{self.DEEZER_URL}/search",
                    params={"q": query},
                    timeout=8.0,
                )
                payload = response.json()
                raise_if_deezer_quota_error(payload)
                items = payload.get("data", [])
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
