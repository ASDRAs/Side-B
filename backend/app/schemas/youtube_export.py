from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

YouTubeBucket = Literal["similar", "reverse", "opposite", "hidden"]
UnmatchedReason = Literal[
    "not_found",
    "unusable_result",
    "low_confidence",
    "duplicate_video",
]


class YouTubeTrackRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    artist: str = Field(min_length=1, max_length=200)


class YouTubeMatchRequest(BaseModel):
    bucket: YouTubeBucket
    tracks: list[YouTubeTrackRequest] = Field(min_length=1, max_length=10)


class YouTubeMatchedTrack(BaseModel):
    name: str
    artist: str
    video_id: str
    youtube_title: str
    channel_title: str
    confidence: float = Field(ge=0.0, le=1.0)
    auto_selected: bool = True
    position: int = Field(ge=0)


class YouTubeUnmatchedTrack(BaseModel):
    name: str
    artist: str
    reason: UnmatchedReason
    position: int = Field(ge=0)


class YouTubeMatchResponse(BaseModel):
    bucket: YouTubeBucket
    requested: int = Field(ge=1, le=10)
    matched: list[YouTubeMatchedTrack]
    unmatched: list[YouTubeUnmatchedTrack]
    deduplicated: int = Field(ge=0)
