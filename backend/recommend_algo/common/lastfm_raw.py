"""Last.fm raw 응답 어댑터.

pylast는 응답 필드를 버린다.

- `Track.get_similar()` -> `SimilarItem(item, match)`. playcount를 버린다.
- `Artist.get_top_tracks()` -> `_get_things()` -> `TopItem(item, playcount)`.
  listeners와 rank를 버린다.
- `Tag.get_top_tracks()`는 같은 `_get_things()`를 쓰는데 tag 응답에는 playcount가
  없어서 weight가 항상 0이 된다.

여기서는 pylast의 HTTP 계층(`_request`)을 그대로 쓰고 파싱만 직접 한다. pylast의
`_collect_nodes`는 `limit`이 차면 1페이지에서 끝나므로 호출 수는 같다. pylast
라이브러리 자체는 수정하지 않는다.

`tag.getTopTracks`는 여기서 다루지 않는다. rank 말고 회수할 필드가 없고, 호출부가
이미 응답 순서로 rank를 세고 있다.
"""

from typing import Any, NamedTuple

import pylast

from recommend_algo.common.models import DiscoverySignals


class ArtistRef(NamedTuple):
    artist_name: str

    def get_name(self) -> str:
        return self.artist_name


class TrackRef(NamedTuple):
    """호출부가 `pylast.Track`에서 실제로 쓰는 두 가지만 제공한다.

    `pylast.Track`을 만들려면 network 객체가 필요하지만 여기서 필요한 것은
    이름 둘뿐이다.
    """

    track_name: str
    artist_name: str

    def get_name(self) -> str:
        return self.track_name

    def get_artist(self) -> ArtistRef:
        return ArtistRef(self.artist_name)


class RawItem(NamedTuple):
    """pylast의 `SimilarItem`/`TopItem`과 같은 모양에 signals를 더한 것.

    `item`과 `match`를 그대로 두는 이유는 호출부가 이미 그 이름으로 읽고 있기
    때문이다. 필드 보존만 하는 단계라 호출부 형태를 바꾸지 않는다.
    """

    item: TrackRef
    match: float
    signals: DiscoverySignals


def signals_of(item: Any) -> DiscoverySignals | None:
    """어댑터가 붙인 신호를 꺼낸다. 아직 pylast 객체인 경로도 통과시킨다."""
    return getattr(item, "signals", None)


def _number(value: str | None) -> int | None:
    """pylast의 `_number`와 달리 미제공을 0으로 만들지 않는다."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ratio(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _rank(node: Any) -> int | None:
    if not node.hasAttribute("rank"):
        return None
    return _number(node.getAttribute("rank"))


def _parse_tracks(
    doc: Any,
    evidence_source: str,
    source_group: str,
) -> list[RawItem]:
    items: list[RawItem] = []
    for node in doc.getElementsByTagName("track"):
        # pylast와 같은 규칙: 첫 <name>이 곡, 두 번째가 <artist> 안의 아티스트다.
        name = pylast._extract(node, "name", 0)
        artist_name = pylast._extract(node, "name", 1)
        if not name or not artist_name:
            continue
        match = _ratio(pylast._extract(node, "match"))
        items.append(
            RawItem(
                item=TrackRef(name, artist_name),
                match=match if match is not None else 0.0,
                signals=DiscoverySignals(
                    similarity_match=match,
                    global_playcount=_number(pylast._extract(node, "playcount")),
                    global_listeners=_number(pylast._extract(node, "listeners")),
                    source_rank=_rank(node),
                    source_group=source_group,
                    evidence_source=evidence_source,
                ),
            )
        )
    return items


def artist_top_tracks(artist: pylast.Artist, limit: int) -> list[RawItem]:
    """`artist.getTopTracks`. listeners와 rank까지 보존한다.

    아티스트 간 비교가 가능한 유일한 노출도 신호(`listeners`)가 나오는 경로다.
    """
    params = artist._get_params()
    params["limit"] = limit
    doc = artist._request("artist.getTopTracks", True, params)
    return _parse_tracks(
        doc,
        evidence_source="artist.getTopTracks",
        source_group=str(artist.get_name() or ""),
    )


def track_similar(track: pylast.Track, limit: int) -> list[RawItem]:
    """`track.getSimilar`. playcount까지 보존한다.

    listeners는 응답에 없다. 후보마다 아티스트 통계를 새로 받으면 금지한
    fan-out이 다시 생기므로 이 경로는 match와 playcount까지만 쓴다.
    """
    params = track._get_params()
    params["limit"] = limit
    doc = track._request("track.getSimilar", True, params)
    seed_artist = str(track.get_artist().get_name() or "")
    return _parse_tracks(
        doc,
        evidence_source="track.getSimilar",
        source_group=f"{seed_artist} - {track.get_name()}",
    )
