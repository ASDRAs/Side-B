import asyncio
import json

import httpx
import pylast

# 기존 프로젝트 모듈 임포트
from app.config import get_settings
from app.services.recommend_service import run_recommend


async def main():
    # 1. 설정 불러오기 (.env 파일에서 API 키 로드)
    settings = get_settings()

    # 2. 테스트할 파라미터 설정
    query = "윤하 혜성"  # 테스트하고 싶은 검색어로 변경하세요!
    top_n = 5  # 각 알고리즘별로 가져올 추천 곡 수

    print(f"🔍 '{query}' 추천 알고리즘 테스트 시작 (top_n={top_n})...")

    # 3. 클라이언트 객체 생성 (pylast 및 httpx)
    lastfm_network = pylast.LastFMNetwork(
        api_key=settings.lastfm_api_key,
        api_secret=settings.lastfm_api_secret,
    )

    # http 타임아웃은 config에 정의된 기본값을 사용하거나 넉넉하게 15초로 설정합니다.
    async with httpx.AsyncClient(timeout=15.0) as http_client:
        try:
            # 4. 추천 함수 호출
            result = await run_recommend(
                query=query, top_n=top_n, http=http_client, lastfm=lastfm_network
            )

            # 5. 결과 예쁘게 출력
            print("\n✅ 추천 완료! 결과 데이터:")
            print(json.dumps(result, indent=2, ensure_ascii=False))

        except Exception as e:
            print(f"\n❌ 실행 중 에러가 발생했습니다: {e}")


if __name__ == "__main__":
    # 비동기 이벤트 루프 실행
    asyncio.run(main())
