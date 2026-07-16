from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 만약 None이 올 수 있는 경우는 아래와 같이
# specific_song: Optional[str] = Field( default=None, description="desc.." )


class DirectSearchAnalysis(BaseModel):
    search_query: str = Field(
        description=(
            "음원 API 검색에 사용할 정규화된 검색어. "
            "곡과 아티스트가 모두 식별되면 '<곡 제목> <아티스트>' 형식을 사용한다."
        )
    )
    track_title: str | None = Field(
        default=None,
        description="사용자 쿼리에서 식별하거나 정규화한 곡 제목",
    )
    artist_name: str | None = Field(
        default=None,
        description="사용자 쿼리에서 식별하거나 정규화한 아티스트 이름",
    )
    alternative_queries: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "원 검색어로 결과를 찾지 못했을 때 사용할 검색어 변형. "
            "영문명, 로마자 표기, 널리 쓰이는 번역 제목 등을 최대 3개 반환한다."
        ),
    )


class MoodAnalysis(BaseModel):
    tags: list[str] = Field(
        min_length=1,
        max_length=5,
        description=(
            "추천 검색에 사용할 lowercase 영문 음악 태그. "
            "장르, 분위기 등의 정보를 중요도 순으로 반환한다."
        ),
    )
    opposite_tags: list[str] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "추천 검색에 사용할 lowercase 영문 음악 태그. "
            "유저 쿼리에 반대되는 장르, 분위기 등의 정보를 중요도 순으로 반환한다."
        ),
    )


class MusicQueryAnalysis(BaseModel):
    intent: Literal["direct", "mood", "meaningless"] = Field(
        description=(
            "특정 곡·아티스트 검색은 direct, 분위기·상황 기반 추천은 mood, "
            "음악 검색 의도를 해석할 수 없으면 meaningless"
        ),
    )
    direct: DirectSearchAnalysis | None = Field(
        default=None,
        description="intent가 direct일 때만 제공하는 검색어 분석 결과",
    )
    mood: MoodAnalysis | None = Field(
        default=None,
        description="intent가 mood일 때만 제공하는 음악 태그 분석 결과",
    )

    @model_validator(mode="after")
    def validate_intent_payload(self) -> "MusicQueryAnalysis":
        if self.intent == "direct":
            if self.direct is None:
                raise ValueError("direct intent requires direct analysis")
            if self.mood is not None:
                raise ValueError("direct intent cannot include mood analysis")

        elif self.intent == "mood":
            if self.mood is None:
                raise ValueError("mood intent requires mood analysis")
            if self.direct is not None:
                raise ValueError("mood intent cannot include direct analysis")

        else:
            if self.direct is not None or self.mood is not None:
                raise ValueError(
                    "meaningless intent cannot include direct or mood analysis"
                )

        return self


class OppositeTagAnalysis(BaseModel):
    opposite_tags: list[str] = Field(min_length=1, max_length=5)
