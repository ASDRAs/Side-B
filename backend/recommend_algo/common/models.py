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
