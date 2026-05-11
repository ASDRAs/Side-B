from typing import Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    useDeterministic: bool = True
    useLlm: bool = True


class Tag(BaseModel):
    name: str
    count: int = Field(default=0, ge=0)


class LastFmLookup(BaseModel):
    artist: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)


class Track(BaseModel):
    providerId: str = Field(..., min_length=1)
    artist: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    albumArt: str
    popularity: int = Field(..., ge=0, le=100)
    tags: list[Tag] = Field(default_factory=list)


SelectedTrack = Track


class ParsedQuery(BaseModel):
    type: Literal["direct", "mood"]
    query: str | None = None
    tags: list[str] = Field(default_factory=list)
    lastfm_candidates: list[LastFmLookup] = Field(default_factory=list)
    raw: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CandidateTrack(BaseModel):
    providerId: str = Field(..., min_length=1)
    artist: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    albumArt: str
    popularity: int = Field(default=0, ge=0, le=100)
    tags: list[Tag] = Field(default_factory=list)
    lastfm_candidates: list[LastFmLookup] = Field(default_factory=list, exclude=True)
    fallback_tags: list[Tag] = Field(default_factory=list, exclude=True)

    def to_track(self) -> Track:
        return Track(
            providerId=self.providerId,
            artist=self.artist,
            title=self.title,
            albumArt=self.albumArt,
            popularity=self.popularity,
            tags=self.tags,
        )
