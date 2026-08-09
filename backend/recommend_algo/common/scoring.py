import math
import random
from bisect import bisect_left, bisect_right
from collections import defaultdict

from app.utils.text import compact_text
from recommend_algo.common.models import TrackInfo

TAG_PRIORITY_STEP = 0.08

# 신호가 없는 후보에게 줄 obscurity. 중립값이라 가점도 감점도 아니다.
UNKNOWN_OBSCURITY = 0.5


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def _exposure_value(track: TrackInfo) -> tuple[float | None, str]:
    """"클수록 더 노출된 곡"이 되는 값과 그 출처를 고른다.

    Last.fm은 엔드포인트마다 다른 것을 준다. 절대 청취자 수가 있으면 그게
    아티스트 간 비교가 되는 유일한 값이라 1순위다. 없으면 재생 수, 그것도 없으면
    응답 내 순위를 쓴다.
    """
    signals = track.signals
    if signals is None:
        return None, "none"
    if signals.global_listeners is not None:
        return float(signals.global_listeners), "listeners"
    if signals.global_playcount is not None:
        return float(signals.global_playcount), "playcount"
    if signals.source_rank is not None:
        # 순위는 작을수록 노출이 크다. 부호를 뒤집어 방향을 맞춘다.
        return float(-signals.source_rank), "tag_rank"
    return None, "none"


# 노출도 신호의 우열. listeners만 아티스트 간 비교가 되므로 가장 강하다.
_SIGNAL_RANK = {"listeners": 3, "playcount": 2, "tag_rank": 1, "none": 0}


def prefers_signals_of(candidate: TrackInfo, current: TrackInfo) -> bool:
    """같은 곡이 두 경로에서 왔을 때 어느 쪽 신호를 쓸지 고른다.

    발견 순서로 고르면 먼저 도는 경로의 약한 신호가 이긴다. reverse는 pool_a
    (track.getSimilar, playcount)를 pool_b(artist.getTopTracks, listeners)보다
    먼저 훑기 때문에 그냥 두면 listeners가 매번 버려진다.
    """
    return _SIGNAL_RANK[_exposure_value(candidate)[1]] > _SIGNAL_RANK[
        _exposure_value(current)[1]
    ]


def _exposure_group(track: TrackInfo, source: str) -> str:
    """같은 척도끼리만 비교하도록 묶는 키.

    listeners와 playcount는 자릿수가 다르고 팬덤이 강한 장르일수록 그 배수가
    커져 섞으면 순서가 뒤집힌다. 순위는 그 응답 안에서만 의미가 있어서 태그가
    다르면 같은 5위라도 절대 노출도가 전혀 다르다.
    """
    if source == "tag_rank" and track.signals is not None:
        return f"tag_rank:{track.signals.source_group}"
    return source


def assign_exposure(tracks: list[TrackInfo]) -> None:
    """후보 풀 안에서 상대 노출도를 매긴다. 0이 최저, 1이 최고 노출이다.

    절대 수치에 전역 임계값을 두지 않는다. 요청마다 풀 구성이 달라 같은 숫자가
    어떤 풀에서는 상위, 다른 풀에서는 하위이기 때문이다. 두 알고리즘 모두 이
    값을 "이 후보들 중 덜 알려진 쪽"을 고르는 데만 쓰므로 풀 내부 백분위면
    충분하다.

    신호가 없는 후보는 None으로 남긴다. 기본값을 채우면 그 값이 판정에 끼어든다.
    예전 `popularity=None -> 55 -> 저노출 보너스`가 정확히 그 사고였다.
    """
    grouped: dict[str, list[tuple[float, TrackInfo]]] = defaultdict(list)
    for track in tracks:
        value, source = _exposure_value(track)
        track.exposure_source = source
        track.exposure_score = None
        if value is not None:
            grouped[_exposure_group(track, source)].append((value, track))

    for group in grouped.values():
        ordered = sorted(value for value, _ in group)
        span = len(ordered) - 1
        # 후보가 하나면 비교할 대상이 없다. 순위를 매길 수 없으므로 중립이다.
        if span <= 0:
            for _, track in group:
                track.exposure_score = 0.5
            continue
        for value, track in group:
            # 동률은 자기들이 차지한 구간의 한가운데를 나눠 갖는다(midrank).
            # 앞쪽 끝을 주면 최상위 동률이 중립으로 내려가 비주류 보너스를 받고,
            # 뒤쪽 끝을 주면 최하위 동률이 마땅한 보너스를 잃는다. 전부 동률인
            # 경우도 이 식이 그대로 0.5를 낸다.
            low = bisect_left(ordered, value)
            high = bisect_right(ordered, value)
            track.exposure_score = ((low + high - 1) / 2) / span


def obscurity_of(track: TrackInfo, *, unknown: float = UNKNOWN_OBSCURITY) -> float:
    """노출도의 반대. 신호가 없으면 중립값이다."""
    if track.exposure_score is None:
        return unknown
    return _clamp_score(1.0 - track.exposure_score)


def _cap_per_artist(tracks: list[TrackInfo], max_per: int = 1) -> list[TrackInfo]:
    seen: dict[str, int] = {}
    result: list[TrackInfo] = []
    for track in tracks:
        key = track.artist.lower()
        if seen.get(key, 0) < max_per:
            seen[key] = seen.get(key, 0) + 1
            result.append(track)
    return result


def _tag_weight(tag_index: int) -> float:
    return max(0.1, 1.0 - (tag_index * TAG_PRIORITY_STEP))


def _weighted_round_robin(
    track_groups: list[list[TrackInfo]],
    limit: int,
    *,
    weights: list[float] | None = None,
    max_per_artist: int | None = None,
    excluded_keys: set[str] | None = None,
) -> list[TrackInfo]:
    """Merge ranked tag results while preserving tag-priority proportions."""
    if limit <= 0 or not track_groups:
        return []
    if max_per_artist is not None and max_per_artist <= 0:
        return []

    resolved_weights = (
        [_tag_weight(index) for index in range(len(track_groups))]
        if weights is None
        else list(weights)
    )
    if len(resolved_weights) != len(track_groups):
        raise ValueError("weights must match track_groups")
    if any(weight <= 0 or not math.isfinite(weight) for weight in resolved_weights):
        raise ValueError("weights must be finite positive numbers")

    canonical_tracks: dict[str, TrackInfo] = {}
    for group in track_groups:
        for track in group:
            key = _track_key(track)
            if not key.strip(":"):
                continue
            existing = canonical_tracks.get(key)
            if existing is None:
                canonical_tracks[key] = track
                continue
            existing.reason_tags = list(
                dict.fromkeys(existing.reason_tags + track.reason_tags)
            )
            existing.match_score = max(
                existing.match_score or 0.0,
                track.match_score or 0.0,
            )

    positions = [0] * len(track_groups)
    current_weights = [0.0] * len(track_groups)
    active = {index for index, group in enumerate(track_groups) if group}
    excluded = excluded_keys or set()
    selected_keys: set[str] = set()
    artist_counts: dict[str, int] = {}
    selected: list[TrackInfo] = []

    while active and len(selected) < limit:
        active_weight = sum(resolved_weights[index] for index in active)
        for index in active:
            current_weights[index] += resolved_weights[index]
        group_index = max(active, key=lambda index: (current_weights[index], -index))
        current_weights[group_index] -= active_weight

        candidate: TrackInfo | None = None
        group = track_groups[group_index]
        while positions[group_index] < len(group):
            track = group[positions[group_index]]
            positions[group_index] += 1
            key = _track_key(track)
            canonical = canonical_tracks.get(key)
            if canonical is None or key in excluded or key in selected_keys:
                continue
            artist_key = canonical.artist.lower()
            if (
                max_per_artist is not None
                and artist_counts.get(artist_key, 0) >= max_per_artist
            ):
                continue
            candidate = canonical
            break

        if candidate is None:
            active.remove(group_index)
            continue

        key = _track_key(candidate)
        artist_key = candidate.artist.lower()
        selected.append(candidate)
        selected_keys.add(key)
        artist_counts[artist_key] = artist_counts.get(artist_key, 0) + 1

    return selected


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
