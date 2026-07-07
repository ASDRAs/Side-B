import httpx
import pylast

from recommend_algo.common import scoring, seeds, sources
from recommend_algo.common.models import TrackInfo


async def tag_based_recommendations(
    query: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n: int = 10,
) -> dict[str, list[TrackInfo]] | None:
    """Fallback for mood/genre queries such as '감성적인 시티팝'."""
    tags = seeds.tags_from_query(query)
    if not tags:
        return None

    primary_rows = await seeds._collect_tag_tracks(
        tags, lastfm, limit=max(top_n * 5, 30)
    )
    pool = scoring._dedupe_tracks([track for rows in primary_rows for track in rows])
    if not pool:
        return None

    # TODO : 리팩 후 field 설정(모든 field 필요한지 확인 필요)
    pool = await sources.get_tracks_metadata(
        http, scoring._cap_per_artist(pool, max_per=3)[: max(top_n * 5, 40)], lastfm
    )
    similar_candidates = scoring._cap_per_artist(pool, max_per=2)
    similar_with_art = [track for track in similar_candidates if track.album_art_url]
    similar = (
        similar_with_art if len(similar_with_art) >= top_n else similar_candidates
    )[:top_n]
    for track in similar:
        tag = track.reason_tags[0] if track.reason_tags else tags[0]
        track.algo, track.label = "tag_similarity", f"#{tag} 태그 추천"
        track.reverse_score = track.match_score

    used_keys = {scoring._track_key(track) for track in similar}
    reverse_pool = [
        track for track in pool if scoring._track_key(track) not in used_keys
    ]
    for index, track in enumerate(reverse_pool):
        obscurity = scoring._popularity_obscurity(
            track.popularity,
            ceiling=scoring.OBSCURITY_CEILING,
        )
        rank_depth = min(1.0, index / max(len(reverse_pool) - 1, 1))
        tag_match = scoring._clamp_score(track.match_score or 0.0)
        track.reverse_score = (
            (obscurity * 0.55) + (rank_depth * 0.25) + (tag_match * 0.20)
        )

    low_exposure_pool = [
        track
        for track in reverse_pool
        if scoring._is_low_exposure(track.popularity)
    ]
    if len(low_exposure_pool) >= top_n:
        reverse_pool = low_exposure_pool
    reverse_candidates = sorted(
        reverse_pool, key=lambda item: item.reverse_score or 0, reverse=True
    )
    reverse_with_art = [track for track in reverse_candidates if track.album_art_url]
    reverse = (
        reverse_with_art if len(reverse_with_art) >= top_n else reverse_candidates
    )[:top_n]
    for track in reverse:
        track.algo, track.label = "tag_reverse", "태그 속 저노출곡"

    used_keys.update(scoring._track_key(track) for track in reverse)
    opposite_tags = seeds._get_opposite_tags(tags, "")
    opposite_rows = await seeds._collect_tag_tracks(
        opposite_tags, lastfm, limit=max(top_n * 3, 20)
    )
    opposite_pool = scoring._dedupe_tracks(
        [
            track
            for rows in opposite_rows
            for track in rows
            if scoring._track_key(track) not in used_keys
        ]
    )
    # TODO : 리팩 후 모든 field 필요한지 확인 필요
    opposite = await sources.get_tracks_metadata(
        http, scoring._cap_per_artist(opposite_pool, max_per=1)[:top_n], lastfm
    )
    for track in opposite:
        tag = track.reason_tags[0] if track.reason_tags else "contrast"
        track.algo, track.label = "tag_opposite", f"#{tag} 반대 결 추천"

    used_keys.update(scoring._track_key(track) for track in opposite)
    hidden_pool = [
        track for track in pool if scoring._track_key(track) not in used_keys
    ]
    hidden_candidates = sorted(
        scoring._cap_per_artist(hidden_pool, max_per=1),
        key=scoring._resolved_popularity,
    )
    hidden_with_art = [track for track in hidden_candidates if track.album_art_url]
    hidden = (hidden_with_art if len(hidden_with_art) >= top_n else hidden_candidates)[
        :top_n
    ]
    for track in hidden:
        track.algo, track.label = "tag_hidden", "태그에서 더 파볼 곡"

    return {
        "similar": similar,
        "reverse": reverse,
        "opposite": opposite,
        "hidden": hidden,
    }
