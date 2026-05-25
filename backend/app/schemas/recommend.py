from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    """
    음악 추천 요청 규격

    - query: 사용자가 입력한 탐색 키워드 (1~200자 제한)
    - top_n: 결과로 반환받을 추천 곡의 개수 (기본값 10, 최소 1개, 최대 50개)
    """

    query: str = Field(..., min_length=1, max_length=200)
    top_n: int = Field(default=10, ge=1, le=50)


class RecommendResponse(BaseModel):
    """
    음악 추천 결과 응답 규격

    - track_name: 기준(Seed) 곡 제목
    - artist: 기준(Seed) 아티스트명
    - top_n: 요청한 추천 곡 개수
    - result: 4가지 버킷별 추천 결과 목록 (similar, reverse, opposite, hidden)
    - source_id: 기준 곡의 외부 API 고유 ID
    - album_art_url: 기준 곡의 앨범 아트 이미지 URL
    """

    track_name: str
    artist: str
    top_n: int
    result: dict
    source_id: str | None = None
    album_art_url: str | None = None
