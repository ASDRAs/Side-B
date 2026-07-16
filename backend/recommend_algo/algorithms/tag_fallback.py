import httpx
import pylast

from recommend_algo.common import scoring, seeds, sources
from recommend_algo.common.models import TrackInfo


async def tag_based_recommendations(
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    tags: list[str],
    opposite_tags: list[str],
    top_n: int = 10,
) -> dict[str, list[TrackInfo]] | None:
    """Fallback for mood/genre queries such as '감성적인 시티팝'."""

    candidates = await seeds._collect_tag_tracks(tags, lastfm, limit=max(top_n * 5, 30))
    candidates = scoring._dedupe_tracks(
        [track for rows in candidates for track in rows]
    )
    if not candidates:
        return None

    # TODO : 리팩 후 field 설정(모든 field 필요한지 확인 필요)
    raw_enriched_candidates = await sources.get_tracks_metadata(
        http,
        scoring._cap_per_artist(candidates, max_per=3)[: max(top_n * 5, 40)],
        lastfm,
    )

    # FIXME : enriched_candidates는 tags 순서대로 되어있음
    # FIXME :예를 들어, tags = [lofi, dance]면 [lofi1, lofi2, ..., dance1, dance2, ...])
    # FIXME :그래서 rank로 정렬하든, 아예 random으로 섞든 or 순서에 따라 가중치를 두든 해야 함.
    enriched_candidates = raw_enriched_candidates

    # 1. 비슷한 느낌의 노래 추천
    # 1-1. 아티스트 당 2곡 씩 추천되도록 하되, 만약 top_n보다 적어지면 drop하지 않고 사용한다.
    drop_by_artist = scoring._cap_per_artist(enriched_candidates, max_per=2)
    similar_tracks = scoring._fill_from_ranked_pool(
        drop_by_artist,
        enriched_candidates,
        top_n,
    )

    for track in similar_tracks:
        tag = track.reason_tags[0] if track.reason_tags else tags[0]
        track.algo, track.label = "tag_similarity", f"#{tag} 태그 추천"
        track.reverse_score = track.match_score

    # 이미 추천된 곡들은 제외하기 위해 관리
    recommended_tracks = {scoring._track_key(track) for track in similar_tracks}

    # 2. 태그 내 저노출곡 추천
    reverse_pool = [
        track
        for track in enriched_candidates
        if scoring._track_key(track) not in recommended_tracks
    ]

    # 2-1. 비주류 점수 계산
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

    # 2-2. 비주류 곡 추천
    low_exposure_pool = [
        track for track in reverse_pool if scoring._is_low_exposure(track.popularity)
    ]
    if len(low_exposure_pool) >= top_n:
        reverse_pool = low_exposure_pool
    reverse_tracks = sorted(
        reverse_pool, key=lambda item: item.reverse_score or 0, reverse=True
    )[:top_n]
    for track in reverse_tracks:
        track.algo, track.label = "tag_reverse", "태그 속 저노출곡"

    recommended_tracks.update(scoring._track_key(track) for track in reverse_tracks)

    # 3. opposite_tags 기반 반대 분위기 추천

    # FIXME : enriched_candidates는 tags와 동일한 case. tags가 여러개여도 맨 앞 tag만 사용
    # 3-1. opposite_tags 기반 노래 검색
    opposite_candidates = await seeds._collect_tag_tracks(
        opposite_tags, lastfm, limit=max(top_n * 3, 20)
    )

    # 3-2. 이미 추천된 곡 & 중복 제거
    opposite_pool = scoring._dedupe_tracks(
        [
            track
            for rows in opposite_candidates
            for track in rows
            if scoring._track_key(track) not in recommended_tracks
        ]
    )
    # 3-3. metadata 추가
    opposite_tracks = await sources.get_tracks_metadata(
        http, scoring._cap_per_artist(opposite_pool, max_per=1)[:top_n], lastfm
    )
    for track in opposite_tracks:
        tag = track.reason_tags[0] if track.reason_tags else "contrast"
        track.algo, track.label = "tag_opposite", f"#{tag} 반대 결 추천"

    recommended_tracks.update(scoring._track_key(track) for track in opposite_tracks)

    # 4. 숨겨진 명곡 추천
    hidden_pool = [
        track
        for track in enriched_candidates
        if scoring._track_key(track) not in recommended_tracks
    ]
    hidden_candidates = sorted(
        scoring._cap_per_artist(hidden_pool, max_per=1),
        key=scoring._resolved_popularity,
    )
    hidden_tracks = hidden_candidates[:top_n]
    for track in hidden_tracks:
        track.algo, track.label = "tag_hidden", "태그에서 더 파볼 곡"

    return {
        "similar": similar_tracks,
        "reverse": reverse_tracks,
        "opposite": opposite_tracks,
        "hidden": hidden_tracks,
    }
