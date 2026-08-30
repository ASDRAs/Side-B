import logging
from typing import Any

import httpx
import pylast

from app.llm.llm_response import OppositeTagAnalysis
from app.llm.llm_wrapper import GeminiWrapper
from app.llm.prompt import OPPOSITE_TAG_PROMPT
from app.utils.text import compact_text, text_ratio
from recommend_algo.common import lastfm_raw, scoring, seeds, sources
from recommend_algo.common.models import TrackInfo

logger = logging.getLogger(__name__)


async def opposite_emotion(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    lastfm: pylast.LastFMNetwork,
    gemini_wrapper: GeminiWrapper,
    top_n=10,
    *,
    excluded_keys: set[str] | None = None,
) -> list[TrackInfo]:
    """
    lastfm에서 track의 tag를 검색하고, 해당 tag의 반대되는 emotion의 track 추천
    만약, track에서 tag를 검색할 수 없는 경우에는 artist를 기준으로 추천
    """
    MAX_TAG_CNT = 8

    # 주어진 track과 artist의 tag를 추출하고 이에 반대되는 opposite tag 추출
    seed_tags = await _seed_tags(track_name, artist, lastfm)
    seed_tags = seed_tags[:MAX_TAG_CNT]
    user_prompt = f"track: {track_name}, artist: {artist}, lastfm_tags: {seed_tags}"
    gemini_response = gemini_wrapper.request(
        system_prompt=OPPOSITE_TAG_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        max_output_tokens=500,
        response_schema=OppositeTagAnalysis,
        response_validator=OppositeTagAnalysis,
    )
    opp_tags = gemini_response.opposite_tags
    excluded = excluded_keys or set()
    collected: list[TrackInfo] = []

    # seed tag와 반대 속성의 tag의 곡들 중에서 인기도 순으로 lastfm에서 검색
    for tag in opp_tags:
        tag_obj = lastfm.get_tag(tag)
        response = await sources._lf_call(
            f"lf:tag_top:{compact_text(tag)}:{top_n * 4}",
            600,
            tag_obj.get_top_tracks,
            top_n * 4,
        )
        for rank, track_metadata in enumerate(response or [], start=1):
            opp_track_name = track_metadata.item.get_name()
            opp_artist = track_metadata.item.get_artist().get_name()
            if _is_same_track(opp_track_name, opp_artist, track_name, artist):
                continue
            candidate = TrackInfo(
                name=opp_track_name,
                artist=opp_artist,
                algo="opposite_emotion",
                label=f"#{tag} 반전 무드",
                signals=lastfm_raw.tag_signals(tag, rank),
            )
            if scoring._track_key(candidate) not in excluded:
                collected.append(candidate)
        collected = scoring._cap_per_artist(
            scoring._dedupe_tracks(collected), max_per=1
        )
        if len(collected) >= top_n:
            break

    # NOTE : get_similar로 검색하고 reverse 해야하는거 아닌지? 수정 필요해 보임
    if not collected:
        lf_track = lastfm.get_track(artist, track_name)

        response = await sources._lf_call(
            f"lf:track_similar:{compact_text(artist)}:{compact_text(track_name)}:{top_n * 4}",
            600,
            lastfm_raw.track_similar,
            lf_track,
            top_n * 4,
        )

        collected = []
        for item in response or []:
            candidate = TrackInfo(
                name=item.item.get_name(),
                artist=item.item.get_artist().get_name(),
                match_score=float(item.match),
                algo="opposite_emotion",
                label="유사곡 기반 반전 추천",
                signals=lastfm_raw.signals_of(item),
            )
            if scoring._track_key(candidate) not in excluded:
                collected.append(candidate)
        collected = scoring._cap_per_artist(
            scoring._dedupe_tracks(collected), max_per=1
        )
    collected = collected[:top_n]
    scoring.assign_exposure(collected)
    # 후보 metadata fan-out 없음. 822209f가 다른 알고리즘에서 걷어낸 호출이고
    # (후보 1곡당 iTunes 1회, 상한 분당 약 20회) 여기만 남아 있었다. 앨범아트는
    # preview 시점에 채운다.
    for track in collected:
        track.algo = track.algo or "opposite_emotion"
        track.label = track.label or "반전 무드 추천"
    return collected


async def _seed_tags(
    track_name: str, artist: str, lastfm: pylast.LastFMNetwork
) -> list[str]:
    """
    lastfm에서 track의 tag를 검색합니다.
    만약, track의 검색된 tag가 없는 경우 artist의 tag를 가져옵니다.
    """
    tags: list[str] = []
    try:
        lf_track = lastfm.get_track(artist, track_name)
        raw_tags = await sources._lf_call(
            f"lf:track_tags:{compact_text(artist)}:{compact_text(track_name)}",
            600,
            lf_track.get_top_tags,
        )
        tags.extend(_tag_names(raw_tags))
    except Exception as exc:
        logger.info("[opposite_emotion] track tags unavailable: %s", exc)

    if not tags:
        try:
            lf_artist = lastfm.get_artist(artist)
            raw_tags = await sources._lf_call(
                f"lf:artist_tags:{compact_text(artist)}",
                600,
                lf_artist.get_top_tags,
            )
            tags.extend(_tag_names(raw_tags))
        except Exception as exc:
            logger.info("[opposite_emotion] artist tags unavailable: %s", exc)

    # if compact_text(artist) in {compact_text(name) for name in _KNOWN_KOREAN_ARTISTS}:
    #    tags.extend(["k-pop", "pop", "female vocalists"])

    # if not tags:
    #    tags.extend(["pop", "indie", "alternative"])

    return seeds._unique_preserve_order(tags)


def _tag_names(raw_tags: Any) -> list[str]:
    names: list[str] = []
    for item in raw_tags or []:
        try:
            name = str(item.item.get_name() or "").strip().lower()
        except Exception:
            name = ""
        if name:
            names.append(name)
    return names


def _is_same_track(name: str, artist: str, seed_name: str, seed_artist: str) -> bool:
    return text_ratio(name, seed_name) > 0.88 and text_ratio(artist, seed_artist) > 0.75
