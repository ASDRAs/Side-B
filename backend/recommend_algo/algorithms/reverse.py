import asyncio
import logging

import httpx
import pylast

from app.utils.text import compact_text
from recommend_algo.common import lastfm_raw, scoring, seeds, sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)


async def _fetch_artist_tracks(src, artist_rank: int) -> list[TrackInfo]:
    synthetic_match = max(0.3, 0.70 - (artist_rank - 1) * 0.1)
    similar_tracks = []
    src_name = compact_text(str(src.get_name()))
    response = await sources._lf_call(
        f"lf:artist_top:{src_name}:20",
        600,
        lastfm_raw.artist_top_tracks,
        src,
        20,
    )

    for raw in response:
        similar_tracks.append(
            TrackInfo(
                name=raw.item.get_name(),
                artist=raw.item.get_artist().get_name(),
                match_score=synthetic_match,
                signals=lastfm_raw.signals_of(raw),
            )
        )
    return similar_tracks


async def reverse_top100(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    top_n=10,
    *,
    prefetched=None,
    excluded_keys: set[str] | None = None,
) -> list[TrackInfo]:
    """
    유저가 검색한 track, artist와 비슷한 노래를 추천하는 함수.
    (1) seed track과 유사한 track, (2) seed artist와 유사한 artist의 대표 track을 기반으로 추천
    이때, 비주류 곡들을 우선으로 추천(재생횟수 및 노래 유사도가 낮은 곡들)
    """
    try:
        if prefetched is not None:
            similar_tracks = prefetched

        # NOTE : 이거 하는 이유가 없어보임. 차라리 track_similar_tracks에 retry 로직 걸어두기
        else:
            _limit = max(60, top_n * 6)
            similar_tracks = await seeds._track_similar_tracks(
                track_name, artist, lastfm, _limit
            )

        # 소스 A : lastfm에서 가져온(pretetched) similar track
        pool_a: list[TrackInfo] = []
        if not isinstance(similar_tracks, Exception):
            pool_a = [
                TrackInfo(
                    name=item.item.get_name(),
                    artist=item.item.get_artist().get_name(),
                    match_score=float(item.match),
                    signals=lastfm_raw.signals_of(item),
                )
                for item in similar_tracks
            ]

        # 소스 B : 유저가 검색한 아티스트와 유사한 아티스트 3명의 상위 20트랙

        # lastfm에서 user query의 artist와 유사한 artist를 가져옴
        similar_artists: list = []
        try:
            artist_lf_info = lastfm.get_artist(artist)
            artist_search_limit = max(3, min(top_n, 8))
            similar_artists = await sources._lf_call(
                f"lf:artist_similar:{compact_text(artist)}:{artist_search_limit}",
                600,
                artist_lf_info.get_similar,
                artist_search_limit,
            )

        except Exception as exc:
            logger.info("[Reverse] similar artist expansion unavailable: %s", exc)

        similar_artists = [sa.item for sa in similar_artists]

        b_results = await asyncio.gather(
            *[
                _fetch_artist_tracks(sa, rank)
                for rank, sa in enumerate(similar_artists, 1)
            ],
            return_exceptions=True,
        )

        pool_b: list[TrackInfo] = []
        for res in b_results:
            if not isinstance(res, Exception):
                pool_b.extend(res)

        # 소스 A,B 병합 및 중복 제거
        input_key = (track_name.lower(), artist.lower())
        excluded = excluded_keys or set()
        kept: dict[str, TrackInfo] = {}
        merged_candidates: list[TrackInfo] = []
        for track in pool_a + pool_b:
            if (track.name.lower(), track.artist.lower()) == input_key:
                continue
            key = scoring._track_key(track)
            if key in excluded:
                continue
            first = kept.get(key)
            if first is None:
                kept[key] = track
                merged_candidates.append(track)
                continue
            # 같은 곡이 두 소스에 다 있으면 순서가 아니라 신호의 질로 고른다.
            # pool_a(track.getSimilar)가 앞이라 그냥 두면 listeners가 있는
            # pool_b(artist.getTopTracks) 쪽이 매번 버려진다.
            if scoring.prefers_signals_of(track, first):
                first.signals = track.signals

        logger.info(
            "[Reverse] 후보 A=%d B=%d 합계=%d",
            len(pool_a),
            len(pool_b),
            len(merged_candidates),
        )

        # match_score 상위 후보를 보강하되 특정 아티스트 쏠림은 줄인다.
        balanced_candidates = scoring._balanced_candidate_slice(
            merged_candidates, top_n * 3
        )
        # 노출도는 Last.fm 응답이 이미 준 값으로 계산한다. 예전에는 여기서
        # 후보 전부를 Deezer에 물어 popularity를 채웠다.
        enriched_candidates = scoring._dedupe_tracks(balanced_candidates)
        scoring.assign_exposure(enriched_candidates)

        # ── 비주류 점수 계산 ───────────────────────────────────────
        # 1단계: 너무 뻔한 상위 추천곡 제외하기 (Obvious Filter)
        # 상위 곡을 최대 top_n개까지 걸러내되, 남는 곡이 top_n 미만이 되지 않도록
        # 후보 여유분만큼만 제거합니다. (후보가 11개일 때 10개를 지워 1곡만 남는 문제 방지)
        drop_count = max(0, min(top_n, len(enriched_candidates) - top_n))
        obvious_keys = {
            scoring._track_key(track) for track in enriched_candidates[:drop_count]
        }

        # 뻔한 곡 목록에 없는 곡들만 새로운 풀에 담습니다.
        discovery_pool = []
        for track in enriched_candidates:
            key = scoring._track_key(track)
            if key not in obvious_keys:
                discovery_pool.append(track)
        pool_size = max(len(discovery_pool) - 1, 1)
        # 비주류 점수 계산
        for rank, track in enumerate(discovery_pool):
            obscurity = scoring.obscurity_of(track)
            match = scoring._clamp_score(track.match_score or 0.0)
            middle_similarity = max(0.0, 1 - (abs(match - 0.35) / 0.45))
            rank_novelty = rank / pool_size
            track.reverse_score = (
                (obscurity * 0.55) + (middle_similarity * 0.30) + (rank_novelty * 0.15)
            )

        sorted_pool = sorted(
            discovery_pool, key=lambda track: track.reverse_score or 0, reverse=True
        )
        ranked = scoring._diverse_top_n(
            scoring._cap_per_artist(sorted_pool, max_per=2), top_n, diversity=0.0
        )
        ranked = scoring._fill_from_ranked_pool(ranked, sorted_pool, top_n)
        for track in ranked:
            track.algo, track.label = "reverse_top100", "당신만 모르는 숨겨진 명곡"
        logger.info("[Reverse] 최종 선정 %d개", len(ranked))
        # 후보 metadata fan-out 없음. 앨범아트는 preview 클릭 시점에 채운다.
        return ranked
    except Exception as exc:
        logger.warning("[reverse_top100] failed: %s", exc)
        return []
