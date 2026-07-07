import asyncio
import logging

import httpx
import pylast

from app.utils.text import compact_text
from recommend_algo.common import scoring, sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)


async def hidden_discovery_by_artist(
    artist: str, http: httpx.AsyncClient, lastfm: pylast.LastFMNetwork, top_n=10
) -> list[TrackInfo]:
    """유저가 입력한 artist와 유사한 artist의 숨은 명곡을 추천하는 함수"""

    HIDDEN_LIMIT = max(top_n * 3, 18)
    try:
        # lastfm에서 유저가 검색한 artist와 유사한 artist를 가져옴
        seed_artist = compact_text(artist)
        lf_artist = lastfm.get_artist(artist)
        response_artists = await sources._lf_call(
            f"lf:artist_similar:{seed_artist}:{HIDDEN_LIMIT}",
            600,
            lf_artist.get_similar,
            HIDDEN_LIMIT,
        )
        artist_candidates = []

        for artist_rank, artist_metadata in enumerate(response_artists or []):
            try:
                similar_artist = artist_metadata.item
                similar_artist_name = str(similar_artist.get_name() or "").strip()
                artist_match = float(getattr(artist_metadata, "match", 0) or 0)
            except Exception as exc:
                logger.warning(
                    "[hidden_discovery] fail to get similar artist & score (%s): %s",
                    similar_artist_name,
                    exc,
                )
                continue

            if (
                not similar_artist_name
                or compact_text(similar_artist_name) == seed_artist
            ):
                continue
            artist_candidates.append(
                (artist_rank, similar_artist, similar_artist_name, artist_match)
            )

        async def _fetch_artist_tracks(artist_infos, max_track_num=4):
            artist_rank, pylast_artist, artist_name, artist_match = artist_infos

            # artist의 노래를 lastfm에서 가져옴
            try:
                response_tracks = await sources._lf_call(
                    f"lf:artist_top:{compact_text(artist_name)}:{max_track_num}",
                    600,
                    pylast_artist.get_top_tracks,
                    max_track_num,
                )
            except Exception as exc:
                logger.warning(
                    "[hidden_discovery] similar artist tracks unavailable (%s): %s",
                    artist_name,
                    exc,
                )
                return []

            # artist의 대표곡들 이름을 가져옴
            results = []
            for track_rank, track_metadata in enumerate(response_tracks or [], start=1):
                try:
                    track_name = str(track_metadata.item.get_name() or "").strip()
                except Exception as exc:
                    logger.warning(
                        "[hidden_discovery] fail to get track info (%s): %s",
                        artist_name,
                        exc,
                    )
                    continue

                if not track_name:
                    continue
                results.append(
                    (
                        artist_rank,
                        track_rank,
                        artist_match,
                        TrackInfo(
                            name=track_name,
                            artist=artist_name,
                            match_score=artist_match,
                            reason_tags=[artist_name],
                        ),
                    )
                )
            return results

        fetched = await asyncio.gather(
            *[_fetch_artist_tracks(row) for row in artist_candidates]
        )
        candidate_tracks = [row for rows in fetched for row in rows]
        if not candidate_tracks:
            return []

        candidates_len = max(len(artist_candidates), 1)
        pre_scored = []

        # 1차 비주류 점수 계산(사전 선별 top_n*3)
        for track_info in candidate_tracks:
            artist_rank, track_rank, artist_match, _ = track_info
            # 아티스트 유사도 & 트랙 depth 기준으로 정렬
            affinity = (
                artist_match
                if artist_match > 0
                else max(0.0, 1 - (artist_rank / candidates_len))
            )
            affinity = max(0.0, min(1.0, affinity))
            depth = min(1.0, max(0.0, (track_rank - 1) / 3))

            # 아티스트 유사도가 높을수록, 대표곡이 아닐수록(depth가 깊을수록) 점수가 높음
            pre_scored.append((affinity * 0.55 + depth * 0.45, track_info))
        pre_scored.sort(key=lambda x: x[0], reverse=True)
        prescored_candidates = [row for _, row in pre_scored[: top_n * 3]]

        tracks_metadata = await sources.get_tracks_metadata(
            http, [row[3] for row in prescored_candidates], fields=["popularity"]
        )

        # 최종 비주류 점수 계산
        for candidate_score, track in zip(prescored_candidates, tracks_metadata):
            artist_rank, track_rank, artist_match, _ = candidate_score
            # 노래 재생횟수도 반영하여 비주류 점수 재계산
            popularity = track.popularity if track.popularity is not None else 55
            obscurity = max(0.0, min(1.0, (80 - popularity) / 80))
            artist_affinity = (
                artist_match
                if artist_match > 0
                else max(0.0, 1 - (artist_rank / candidates_len))
            )
            artist_affinity = max(0.0, min(1.0, artist_affinity))
            track_depth = min(1.0, max(0.0, (track_rank - 1) / 3))
            track.reverse_score = (
                (artist_affinity * 0.35) + (obscurity * 0.45) + (track_depth * 0.20)
            )

        # 노출도가 낮은 track을 우선적으로 추천
        ranked_pool = sorted(
            tracks_metadata, key=lambda item: item.reverse_score or 0, reverse=True
        )
        low_exposure_pool = [
            track
            for track in ranked_pool
            if (track.popularity if track.popularity is not None else 55) < 70
        ]
        if len(low_exposure_pool) >= top_n:
            ranked_pool = low_exposure_pool

        ranked = scoring._cap_per_artist(scoring._dedupe_tracks(ranked_pool), max_per=1)[
            :top_n
        ]
        ranked = await sources.get_tracks_metadata(
            http, ranked, lastfm, fields=["album_art", "source_id"]
        )
        for track in ranked:
            track.algo, track.label = "hidden_discovery", "닮은 아티스트의 발견곡"
        return ranked
    except Exception as exc:
        logger.warning("[hidden_discovery] failed: %s", exc)
        return []
