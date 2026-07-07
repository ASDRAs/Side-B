from dataclasses import dataclass, field


@dataclass
class TrackInfo:
    name: str
    artist: str
    source_id: str | None = None
    album_art_url: str | None = None
    popularity: int | None = None
    match_score: float | None = None
    reverse_score: float | None = None
    algo: str = ""
    label: str = ""
    reason_tags: list[str] = field(default_factory=list)
