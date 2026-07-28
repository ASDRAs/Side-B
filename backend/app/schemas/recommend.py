from typing import Literal

from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    """
    음악 추천 요청 규격

    - query: 사용자가 입력한 탐색 키워드 (1~200자 제한)
    - top_n: 추천 트랙별 곡 수 (10곡으로 고정)
    """

    query: str = Field(..., min_length=1, max_length=200)
    top_n: Literal[10] = 10


class RecommendResponse(BaseModel):
    """
    음악 추천 결과 응답 규격

    - track_name: 기준(Seed) 곡 제목
    - artist: 기준(Seed) 아티스트명
    - top_n: 요청한 추천 곡 개수
    - result: 검색 의도별 3가지 버킷의 추천 결과
      - direct: similar, reverse, hidden
      - mood: similar, opposite, hidden
    - source_id: 기준 곡의 외부 API 고유 ID
    - album_art_url: 기준 곡의 앨범 아트 이미지 URL
    """

    track_name: str
    artist: str
    top_n: Literal[10]
    result: dict
    source_id: str | None = None
    album_art_url: str | None = None
