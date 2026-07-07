import math
import random

from app.utils.text import compact_text
from recommend_algo.common.models import TrackInfo

DEFAULT_POPULARITY = 55
LOW_EXPOSURE_CUTOFF = 70
OBSCURITY_CEILING = 80


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def _resolved_popularity(
    track: TrackInfo,
    *,
    default: int = DEFAULT_POPULARITY,
) -> int:
    return track.popularity if track.popularity is not None else default


def _popularity_obscurity(
    popularity: int | None,
    *,
    ceiling: int,
    default: int = DEFAULT_POPULARITY,
) -> float:
    resolved = popularity if popularity is not None else default
    return _clamp_score((ceiling - resolved) / ceiling)


def _is_low_exposure(
    popularity: int | None,
    *,
    cutoff: int = LOW_EXPOSURE_CUTOFF,
    default: int = DEFAULT_POPULARITY,
) -> bool:
    resolved = popularity if popularity is not None else default
    return resolved < cutoff


def _cap_per_artist(tracks: list[TrackInfo], max_per: int = 1) -> list[TrackInfo]:
    seen: dict[str, int] = {}
    result: list[TrackInfo] = []
    for track in tracks:
        key = track.artist.lower()
        if seen.get(key, 0) < max_per:
            seen[key] = seen.get(key, 0) + 1
            result.append(track)
    return result


def _diverse_top_n(
    pool: list[TrackInfo],
    top_n: int,
    *,
    score_fn=lambda t: t.reverse_score or 0,
    diversity: float = 0.3,
    candidate_mult: int = 3,
) -> list[TrackInfo]:
    """점수 정규화 후 Gaussian noise로 샘플링 — 같은 입력에도 매번 다른 결과."""
    if not pool:
        return []
    sorted_pool = sorted(pool, key=score_fn, reverse=True)
    if diversity <= 0.0:
        return sorted_pool[:top_n]
    n_candidates = min(len(pool), top_n * candidate_mult)
    candidates = sorted_pool[:n_candidates]
    raw_scores = [score_fn(t) for t in candidates]
    min_s, max_s = min(raw_scores), max(raw_scores)
    score_range = (max_s - min_s) or 1.0
    scored = [
        (t, (s - min_s) / score_range + random.gauss(0, diversity * 0.3))
        for t, s in zip(candidates, raw_scores)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:top_n]]


def _balanced_candidate_slice(
    tracks: list[TrackInfo],
    limit: int,
    *,
    score_fn=lambda t: t.match_score or 0,
) -> list[TrackInfo]:
    sorted_pool = sorted(tracks, key=score_fn, reverse=True)
    if len(sorted_pool) <= limit:
        return sorted_pool

    artist_count = len(
        {compact_text(track.artist) for track in sorted_pool if track.artist}
    )
    per_artist = max(2, math.ceil(limit / max(artist_count, 1)))
    balanced = _cap_per_artist(sorted_pool, max_per=per_artist)
    selected_keys = {_track_key(track) for track in balanced}
    if len(balanced) < limit:
        balanced.extend(
            track for track in sorted_pool if _track_key(track) not in selected_keys
        )
    return balanced[:limit]


def _fill_from_ranked_pool(
    primary: list[TrackInfo],
    fallback_pool: list[TrackInfo],
    top_n: int,
) -> list[TrackInfo]:
    selected = list(primary[:top_n])
    if len(selected) >= top_n:
        return selected
    selected_keys = {_track_key(track) for track in selected}
    for track in fallback_pool:
        key = _track_key(track)
        if key in selected_keys:
            continue
        selected.append(track)
        selected_keys.add(key)
        if len(selected) >= top_n:
            break
    return selected


def _dedupe_tracks(tracks: list[TrackInfo]) -> list[TrackInfo]:
    """
    track list를 입력받아 중복되는 track을 제거하고 return합니다.
    """
    seen: set[str] = set()
    deduped: list[TrackInfo] = []
    for track in tracks:
        key = _track_key(track)
        if not key.strip(":") or key in seen:
            continue
        seen.add(key)
        deduped.append(track)
    return deduped


def _track_key(track: TrackInfo) -> str:
    return f"{compact_text(track.artist)}::{compact_text(track.name)}"
