from pydantic import BaseModel, ConfigDict, Field


class GenreClassificationRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    track_name: str = Field(
        min_length=1,
        max_length=200,
    )
    artist: str = Field(
        min_length=1,
        max_length=200,
    )


class GenreClassificationResponse(BaseModel):
    track_name: str
    artist: str
    genre: str
    score: float
