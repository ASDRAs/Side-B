import asyncio
import html
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pykakasi import kakasi

from app.services.youtube.client import YouTubeSearchClient
from app.utils.text import compact_text, text_ratio
from app.utils.track_matching import (
    artist_score,
    clean_title,
    contains_bad_version_marker,
    identity_qualifiers_match,
    looks_like_bad_version,
    strict_title_ratio,
    version_markers,
)

MatchFailureReason = Literal["not_found", "unusable_result", "low_confidence"]

_OFFICIAL_MARKERS = (
    "official audio",
    "official video",
    "official music video",
    "official mv",
    "m/v",
    "mv",
)
_BAD_DERIVATIVE_MARKERS = (
    "karaoke",
    "instrumental",
    "tribute",
    "cover",
    "reaction",
    "MR",
    "musicmaru",
    "뮤직마루",
    "노래방",
    "반주",
)
_ALTERED_SPEED_MARKERS = ("nightcore", "sped up", "slowed")
_COVER_SUFFIX_PATTERN = re.compile(
    r"(?<!\w)(?:(?:piano|guitar|band|vocal|drum|bass|acoustic)\s+)?"
    r"cover(?:\s+by\b.*)?\s*$",
    re.IGNORECASE,
)
_COVER_BRACKET_PATTERN = re.compile(
    r"[\[(【][^)\]】]*(?<!\w)cover(?!\w)[^)\]】]*[)\]】]",
    re.IGNORECASE,
)
_HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
_JAPANESE_PATTERN = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_JAPANESE_ROMANIZER = kakasi()


@dataclass(frozen=True, slots=True)
class YouTubeMatch:
    video_id: str
    youtube_title: str
    channel_title: str
    confidence: float


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    match: YouTubeMatch | None
    reason: MatchFailureReason | None = None


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: float
    outcome: MatchOutcome


@dataclass(slots=True)
class _InflightEntry:
    task: asyncio.Task[MatchOutcome]
    waiters: int = 0


def _candidate_fields(item: dict[str, Any]) -> tuple[str, str, str] | None:
    identity = item.get("id")
    snippet = item.get("snippet")
    video_id = identity.get("videoId") if isinstance(identity, dict) else None
    if not video_id or not isinstance(snippet, dict):
        return None
    title = html.unescape(str(snippet.get("title") or "")).strip()
    channel = html.unescape(str(snippet.get("channelTitle") or "")).strip()
    if not title:
        return None
    return str(video_id), title, channel


def _text_variants(value: str) -> tuple[str, ...]:
    """Return the original spelling and a Japanese Hepburn transliteration.

    YouTube commonly exposes Japanese official channel names while Last.fm and
    iTunes return the same artist in Latin characters. Transliteration gives us
    evidence that those spellings identify the same name without weakening the
    confidence threshold for unrelated artists.
    """
    if not _JAPANESE_PATTERN.search(value):
        return (value,)

    romanized = "".join(
        str(part.get("hepburn") or part.get("orig") or "")
        for part in _JAPANESE_ROMANIZER.convert(value)
    ).strip()
    values = [value]
    if romanized and compact_text(romanized) != compact_text(value):
        values.append(romanized)
    return tuple(values)


def _variant_text_ratio(candidate: str, expected: str) -> float:
    if _HANGUL_PATTERN.search(candidate) and _HANGUL_PATTERN.search(expected):
        return strict_title_ratio(candidate, expected)
    return text_ratio(candidate, expected)


def _leading_text_match(candidate: str, expected: str) -> re.Match[str] | None:
    value = expected.strip()
    if not value:
        return None
    return re.match(
        rf"^\s*{re.escape(value)}(?!\w)",
        candidate,
        re.IGNORECASE,
    )


def _korean_artist_prefixed_title_fragment(
    candidate_title: str,
    expected_title: str,
    expected_artist: str,
) -> str | None:
    """Extract a delimiter-free Korean title from the candidate title field.

    Domain: title field only; Hangul artist and title; exact string-prefix artist
    and exact word-boundary title, separated by whitespace and an optional
    parenthesized artist alias. Other scripts and title positions stay on the
    existing matcher paths.
    """
    if not (
        _HANGUL_PATTERN.search(expected_artist)
        and _HANGUL_PATTERN.search(expected_title)
    ):
        return None

    expected_title_variants = _text_variants(expected_title)
    for artist_variant in _text_variants(expected_artist):
        artist_match = _leading_text_match(candidate_title, artist_variant)
        if not artist_match:
            continue
        remainder = candidate_title[artist_match.end() :]
        remainder = re.sub(r"^\s*\([^)]*\)\s*", "", remainder, count=1)
        for title_variant in expected_title_variants:
            title_match = _leading_text_match(remainder, title_variant)
            if title_match:
                return remainder[title_match.start() : title_match.end()].strip()
    return None


def _title_score(
    candidate_title: str,
    expected_title: str,
    artist_prefixed_fragment: str | None = None,
) -> float:
    fragments = [candidate_title]
    fragments.extend(
        part.strip()
        for part in re.split(r"\s+(?:[|:_-])\s+", candidate_title)
        if part.strip()
    )
    fragments.extend(
        match.strip()
        for match in re.findall(r"['\"‘’“”]([^'\"‘’“”]+)['\"‘’“”]", candidate_title)
        if match.strip()
    )
    if artist_prefixed_fragment:
        fragments.append(artist_prefixed_fragment)
    expected_variants = _text_variants(expected_title)
    return max(
        (
            _variant_text_ratio(
                clean_title(fragment_variant),
                clean_title(expected_variant),
            )
            if identity_qualifiers_match(fragment_variant, expected_variant)
            else 0.0
            for fragment in fragments
            for fragment_variant in _text_variants(fragment)
            for expected_variant in expected_variants
        ),
        default=0.0,
    )


def _channel_artist(channel_title: str) -> str:
    value = re.sub(r"\s*-\s*topic\s*$", "", channel_title, flags=re.I)
    value = re.sub(r"\bofficial\b", "", value, flags=re.I)
    value = re.sub(r"vevo\s*$", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip(" -")


def _title_artist_candidates(candidate_title: str) -> list[str]:
    value = re.sub(r"^\s*\[(?:mv|m/v|official)\]\s*", "", candidate_title, flags=re.I)
    candidates: list[str] = []
    for separator in (r"\s+-\s+", r"\s+_\s+"):
        prefix = re.split(separator, value, maxsplit=1)
        if len(prefix) == 2 and prefix[0].strip():
            candidates.append(prefix[0].strip())
    quoted = re.split(r"\s+['\"‘“]", value, maxsplit=1)
    if len(quoted) == 2 and quoted[0].strip():
        candidates.append(quoted[0].strip())
    return candidates


def _starts_with_artist(candidate: str, artist: str) -> bool:
    expected = artist.strip()
    match = _leading_text_match(candidate, expected)
    if not match:
        return False
    remainder = candidate[match.end() :].strip(" -_()[]")
    if not remainder:
        return True
    expected_scripts = _letter_scripts(expected)
    remainder_scripts = _letter_scripts(remainder)
    return bool(
        expected_scripts
        and remainder_scripts
        and expected_scripts.isdisjoint(remainder_scripts)
    )


def _letter_scripts(value: str) -> set[str]:
    scripts: set[str] = set()
    for char in value:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        family = next(
            (
                marker
                for marker in ("LATIN", "HANGUL", "HIRAGANA", "KATAKANA", "CJK")
                if marker in name
            ),
            "OTHER",
        )
        scripts.add(family)
    return scripts


def _artist_score(candidate_title: str, channel_title: str, artist: str) -> float:
    candidates = [
        _channel_artist(channel_title),
        *_title_artist_candidates(candidate_title),
    ]
    expected_variants = _text_variants(artist)

    def variant_score(candidate: str, expected: str) -> float:
        if _starts_with_artist(candidate, expected):
            return 1.0
        score = artist_score(candidate, (expected,), candidate_title)
        if (
            score < 1.0
            and _HANGUL_PATTERN.search(candidate)
            and _HANGUL_PATTERN.search(expected)
        ):
            return strict_title_ratio(candidate, expected)
        return score

    return max(
        (
            variant_score(candidate_variant, expected)
            for candidate in candidates
            if candidate
            for candidate_variant in _text_variants(candidate)
            for expected in expected_variants
        ),
        default=0.0,
    )


def _contains_derivative_marker(
    value: str,
    marker: str,
    *,
    title_context: bool = True,
) -> bool:
    if contains_bad_version_marker(value, marker, title_context=title_context):
        return True
    return bool(
        title_context
        and marker.casefold() == "cover"
        and (
            _COVER_SUFFIX_PATTERN.search(value) or _COVER_BRACKET_PATTERN.search(value)
        )
    )


def _looks_like_derivative(value: str, *, title_context: bool = True) -> bool:
    return looks_like_bad_version(value, title_context=title_context) or bool(
        title_context
        and (
            _COVER_SUFFIX_PATTERN.search(value) or _COVER_BRACKET_PATTERN.search(value)
        )
    )


def score_candidate(
    item: dict[str, Any],
    expected_title: str,
    expected_artist: str,
) -> YouTubeMatch | None:
    fields = _candidate_fields(item)
    if not fields:
        return None
    video_id, title, channel = fields
    lowered_title = title.lower()
    expected = expected_title.lower()
    topic_channel = bool(re.search(r"\s-\s*topic\s*$", channel, re.I))
    official_channel = (
        topic_channel
        or bool(re.search(r"\bofficial\b", channel, re.I))
        or bool(re.search(r"vevo\s*$", channel, re.I))
    )
    official_marker = topic_channel or any(
        re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", lowered_title, re.I)
        for marker in _OFFICIAL_MARKERS
    )

    artist_prefixed_fragment = _korean_artist_prefixed_title_fragment(
        title,
        expected_title,
        expected_artist,
    )
    title_match_score = _title_score(
        title,
        expected_title,
        artist_prefixed_fragment,
    )
    artist_match_score = _artist_score(title, channel, expected_artist)
    if artist_prefixed_fragment:
        artist_match_score = 1.0

    score = title_match_score * 0.53
    score += artist_match_score * 0.35
    score += 0.07 if official_channel else 0.0
    score += 0.05 if official_marker else 0.0

    if any(
        (
            _contains_derivative_marker(title, marker)
            or _contains_derivative_marker(channel, marker, title_context=False)
        )
        and not _contains_derivative_marker(expected_title, marker)
        and not _contains_derivative_marker(
            expected_artist, marker, title_context=False
        )
        for marker in _BAD_DERIVATIVE_MARKERS
    ):
        score -= 0.35
    if any(
        re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", lowered_title, re.I)
        and marker not in expected
        for marker in _ALTERED_SPEED_MARKERS
    ):
        score -= 0.30
    if version_markers(title) != version_markers(expected_title):
        score -= 0.20
    candidate_has_bad_version = _looks_like_derivative(title) or _looks_like_derivative(
        channel,
        title_context=False,
    )
    if (
        candidate_has_bad_version
        and not _looks_like_derivative(expected_title)
        and not _looks_like_derivative(expected_artist, title_context=False)
    ):
        # A translated Topic title with an exact artist bottoms out at 0.47.
        # Keep unrequested covers below that so the official upload remains the
        # review candidate even when only the cover repeats the source title.
        score = min(score, 0.44)

    return YouTubeMatch(
        video_id=video_id,
        youtube_title=title,
        channel_title=channel,
        confidence=round(max(0.0, min(score, 1.0)), 4),
    )


def select_best_candidate(
    items: list[dict[str, Any]],
    expected_title: str,
    expected_artist: str,
) -> YouTubeMatch | None:
    best: YouTubeMatch | None = None
    for item in items:
        candidate = score_candidate(item, expected_title, expected_artist)
        if candidate and (best is None or candidate.confidence > best.confidence):
            best = candidate
    return best


class YouTubeMatcher:
    def __init__(
        self,
        client: YouTubeSearchClient,
        *,
        threshold: float = 0.85,
        review_threshold: float = 0.40,
        concurrency: int = 3,
        positive_ttl_seconds: float = 3600.0,
        negative_ttl_seconds: float = 600.0,
        max_cache_size: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.threshold = threshold
        self.review_threshold = min(threshold, max(0.0, review_threshold))
        self.positive_ttl_seconds = positive_ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        self.max_cache_size = max_cache_size
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._cache: OrderedDict[tuple[str, str], _CacheEntry] = OrderedDict()
        self._inflight: dict[tuple[str, str], _InflightEntry] = {}

    @staticmethod
    def cache_key(name: str, artist: str) -> tuple[str, str]:
        return compact_text(artist), compact_text(name)

    def _get_cached(self, key: tuple[str, str]) -> MatchOutcome | None:
        entry = self._cache.get(key)
        if not entry:
            return None
        if entry.expires_at <= self._clock():
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return entry.outcome

    def _store(self, key: tuple[str, str], outcome: MatchOutcome) -> None:
        ttl = (
            self.positive_ttl_seconds
            if outcome.match and outcome.reason is None
            else self.negative_ttl_seconds
        )
        self._cache[key] = _CacheEntry(self._clock() + ttl, outcome)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)

    async def _match_uncached(
        self, key: tuple[str, str], name: str, artist: str
    ) -> MatchOutcome:
        async with self._semaphore:
            items = await self.client.search(name, artist)
        if not items:
            outcome = MatchOutcome(match=None, reason="not_found")
        else:
            best = select_best_candidate(items, name, artist)
            if best is None:
                outcome = MatchOutcome(match=None, reason="unusable_result")
            elif best.confidence >= self.threshold:
                outcome = MatchOutcome(match=best)
            elif best.confidence >= self.review_threshold:
                # Keep the best candidate visible for explicit user review. The
                # API marks it as not auto-selected, so it never silently enters
                # a playlist but can still be accepted by a human.
                outcome = MatchOutcome(match=best, reason="low_confidence")
            else:
                outcome = MatchOutcome(match=None, reason="low_confidence")
        self._store(key, outcome)
        return outcome

    async def match_track(self, name: str, artist: str) -> MatchOutcome:
        key = self.cache_key(name, artist)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        entry = self._inflight.get(key)
        if entry is None:
            task = asyncio.create_task(self._match_uncached(key, name, artist))
            entry = _InflightEntry(task=task)
            self._inflight[key] = entry

            def clear_inflight(completed: asyncio.Task[MatchOutcome]) -> None:
                if self._inflight.get(key) is entry:
                    del self._inflight[key]

            task.add_done_callback(clear_inflight)
        entry.waiters += 1
        cancelled = False
        try:
            return await asyncio.shield(entry.task)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            entry.waiters -= 1
            if cancelled and entry.waiters == 0 and not entry.task.done():
                if self._inflight.get(key) is entry:
                    del self._inflight[key]
                entry.task.cancel()
