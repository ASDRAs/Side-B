from dataclasses import dataclass, field
from typing import Any, Literal, get_args

ProviderName = Literal["itunes", "lastfm", "deezer"]
MatchBasis = Literal["provider_id", "isrc", "strict_text"]

_PROVIDER_NAMES: frozenset[str] = frozenset(get_args(ProviderName))

# 외부 응답이 쓰는 대표 source_id의 우선순위. 내부 판정은 bindings를 직접 본다.
_SOURCE_ID_PRIORITY: tuple[ProviderName, ...] = ("itunes", "deezer", "lastfm")


@dataclass(frozen=True)
class ProviderBinding:
    """한 공급자가 확정한 곡.

    공급자마다 독립된 슬롯을 쓰므로 iTunes ID와 Deezer ID가 서로를 덮어쓰지
    않는다. 두 ID의 숫자를 직접 비교하는 것은 의미가 없다(각자 다른 namespace).
    """

    provider: ProviderName
    provider_track_id: str
    resolved_title: str = ""
    resolved_artist: str = ""
    isrc: str | None = None
    match_basis: MatchBasis = "strict_text"

    @property
    def source_id(self) -> str:
        return f"{self.provider}:{self.provider_track_id}"


def binding_from_source_id(
    source_id: str | None,
    resolved_title: str = "",
    resolved_artist: str = "",
) -> ProviderBinding | None:
    """`itunes:123` 형태의 기존 표기를 binding으로 되돌린다."""
    provider, _, track_id = str(source_id or "").partition(":")
    if provider not in _PROVIDER_NAMES or not track_id:
        return None
    return ProviderBinding(
        provider=provider,  # type: ignore[arg-type]
        provider_track_id=track_id,
        resolved_title=resolved_title,
        resolved_artist=resolved_artist,
        match_basis="provider_id",
    )


@dataclass(frozen=True)
class DiscoverySignals:
    """Last.fm 응답이 실제로 준 값.

    엔드포인트마다 주는 필드가 다르다. 없는 값은 `None`이지 0이 아니다 —
    `tag.getTopTracks`에는 playcount가 아예 없는데 pylast는 그걸 0으로 만든다.
    0과 미제공을 구분하지 못하면 노출도 점수가 조용히 틀어진다.

    | 엔드포인트            | 주는 것                           |
    |----------------------|-----------------------------------|
    | `track.getSimilar`   | match, playcount                  |
    | `artist.getTopTracks`| listeners, playcount, rank        |
    | `tag.getTopTracks`   | rank만                            |

    `source_rank`는 그 응답 안에서의 순위다. 아티스트 간 절대 노출도로 비교하면
    안 된다. hidden 경로는 아티스트당 상위 4곡만 받아서 모든 후보의 rank가
    1~4에 몰린다.
    """

    similarity_match: float | None = None
    global_playcount: int | None = None
    global_listeners: int | None = None
    source_rank: int | None = None
    source_group: str | None = None
    evidence_source: str = ""


@dataclass
class TrackInfo:
    """곡의 정체성과 추천 점수를 함께 들고 다니는 내부 표현.

    `name`·`artist`가 canonical 값이다. 공급자가 반환한 실제 표기는 각
    `ProviderBinding.resolved_*`에 남는다.
    """

    name: str
    artist: str
    album_art_url: str | None = None
    popularity: int | None = None
    match_score: float | None = None
    reverse_score: float | None = None
    algo: str = ""
    label: str = ""
    reason_tags: list[str] = field(default_factory=list)
    # remix/live/acoustic 등 다른 recording은 별도 곡으로 유지하기 위한 자리.
    recording_variant: str | None = None
    bindings: dict[ProviderName, ProviderBinding] = field(default_factory=dict)
    # 이 후보를 발견한 Last.fm 응답의 원시 증거. 아직 점수에 쓰지 않는다.
    signals: DiscoverySignals | None = None

    def bind(self, binding: ProviderBinding | None) -> None:
        """공급자 슬롯에 기록한다. 같은 공급자의 재확정만 덮어쓴다."""
        if binding is not None:
            self.bindings[binding.provider] = binding

    @property
    def source_id(self) -> str | None:
        """외부 호환용 대표 ID."""
        return next(
            (
                self.bindings[provider].source_id
                for provider in _SOURCE_ID_PRIORITY
                if provider in self.bindings
            ),
            None,
        )


def track_to_api_dict(track: TrackInfo) -> dict[str, Any]:
    """외부 응답 형식.

    `bindings`와 `recording_variant`는 내부 표현이라 공개하지 않는다. provider별
    ID 공개는 Chrome extension과의 계약이 정해질 때 typed schema로 따로 다룬다.
    """
    return {
        "name": track.name,
        "artist": track.artist,
        "source_id": track.source_id,
        "album_art_url": track.album_art_url,
        "popularity": track.popularity,
        "match_score": track.match_score,
        "reverse_score": track.reverse_score,
        "algo": track.algo,
        "label": track.label,
        "reason_tags": list(track.reason_tags),
    }
