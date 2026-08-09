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

    tags = seeds._unique_preserve_order(tags)
    opposite_tags = seeds._unique_preserve_order(opposite_tags)
    candidate_groups = await seeds._collect_tag_tracks(
        tags, lastfm, limit=max(top_n * 5, 30)
    )
    candidates = scoring._weighted_round_robin(
        candidate_groups,
        max(top_n * 5, 40),
        max_per_artist=3,
    )
    if not candidates:
        return None

    # 태그 안에서의 상대 노출도를 매긴다. `tag.getTopTracks`는 태그별 인기순
    # 정렬이라 순위가 곧 그 무드 안에서의 노출도다. 태그가 여러 개 섞이므로
    # assign_exposure가 태그별로 따로 백분위를 낸다.
    #
    # 예전에는 여기서 후보 50개를 Deezer에 물어 절대 popularity를 채웠다. 그
    # 값의 상당수가 K-pop 미수록으로 결측이었고, 결측이 기본값 55로 채워져
    # 오히려 저노출 보너스를 받았다.
    enriched_candidates = candidates
    scoring.assign_exposure(enriched_candidates)

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

    # 2. 태그 유사도는 유지하면서 저노출곡과 숨은 명곡 역할을 하나로 합친다.
    hidden_pool = [
        track
        for track in enriched_candidates
        if scoring._track_key(track) not in recommended_tracks
    ]

    # 이 버킷의 "숨은 곡"은 절대 무명곡이 아니라 그 무드 안에서 덜 상위인 곡이다.
    # 절대 노출도를 줄 공급자가 없어서가 아니라, 태그를 듣는 사람 기준의 저노출이
    # 이 버킷이 원하는 것이기 때문이다.
    #
    # 이전에는 병합 풀에서의 위치(rank_depth)와 태그 순위(tag_match)가 같은
    # 신호를 반대 방향으로 두 번 세고 있었다. 태그별 정확한 순위 하나로 합친다.
    for track in hidden_pool:
        depth = scoring.obscurity_of(track)
        tag_match = scoring._clamp_score(track.match_score or 0.0)
        track.reverse_score = (depth * 0.80) + (tag_match * 0.20)

    ranked_hidden_pool = sorted(
        hidden_pool, key=lambda item: item.reverse_score or 0, reverse=True
    )
    diverse_hidden_pool = scoring._cap_per_artist(ranked_hidden_pool, max_per=1)
    hidden_tracks = scoring._fill_from_ranked_pool(
        diverse_hidden_pool,
        ranked_hidden_pool,
        top_n,
    )
    for track in hidden_tracks:
        track.algo, track.label = "tag_hidden", "태그 속 숨은 발견곡"

    recommended_tracks.update(scoring._track_key(track) for track in hidden_tracks)

    # 3. opposite_tags 기반 반대 분위기 추천

    # 3-1. opposite_tags 기반 노래 검색
    opposite_candidate_groups = await seeds._collect_tag_tracks(
        opposite_tags, lastfm, limit=max(top_n * 3, 20)
    )

    # 3-2. 이미 추천된 곡 & 중복 제거
    opposite_tracks = scoring._weighted_round_robin(
        opposite_candidate_groups,
        top_n,
        max_per_artist=1,
        excluded_keys=recommended_tracks,
    )
    # 반대 태그 후보는 별도 풀이라 위 assign_exposure에 포함되지 않았다. 순위
    # 신호가 있는데 계산만 빠지면 응답에서 "미수록"과 구분되지 않는다.
    scoring.assign_exposure(opposite_tracks)
    for track in opposite_tracks:
        tag = track.reason_tags[0] if track.reason_tags else "contrast"
        track.algo, track.label = "tag_opposite", f"#{tag} 반대 결 추천"

    recommended_tracks.update(scoring._track_key(track) for track in opposite_tracks)

    selected_tracks = scoring._dedupe_tracks(
        similar_tracks + opposite_tracks + hidden_tracks
    )
    await sources.get_tracks_metadata(
        http,
        selected_tracks,
        lastfm,
        fields=["album_art", "source_id"],
    )

    return {
        "similar": similar_tracks,
        "opposite": opposite_tracks,
        "hidden": hidden_tracks,
    }
