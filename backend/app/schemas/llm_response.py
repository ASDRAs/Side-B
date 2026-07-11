from typing import Literal

from pydantic import BaseModel, Field

# 만약 None이 올 수 있는 경우는 아래와 같이
# specific_song: Optional[str] = Field( default=None, description="desc.." )


class QueryClassifySchema(BaseModel):
    type: Literal["mood", "direct", "meaningless"] = Field(
        description="유저의 검색 의도 분류. 추천성/추상적 요구는 'mood', 특정 곡명/아티스트 직접 검색은 'direct', 의미가 없는 경우에는 'meaningless'"
    )
