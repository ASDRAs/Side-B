import pytest

from recommend_algo.common import sources


@pytest.fixture(autouse=True)
def _clear_lastfm_cache():
    """`_lf_call` 캐시는 모듈 전역이라 테스트 사이에 새어 나간다.

    캐시 키가 seed 곡명·아티스트로 만들어지는데 여러 테스트가 같은 "Seed" /
    "Seed Artist"를 쓴다. 그래서 뒤 테스트가 앞 테스트의 Last.fm 응답을 물려받고,
    자기 fake가 준 후보 대신 남의 후보로 채점된다. TTL이 600초라 한 번 걸리면
    실행 내내 남는다.
    """
    sources._cache.clear()
    yield
    sources._cache.clear()


@pytest.fixture(autouse=True)
def _reset_lastfm_rate_state():
    """차단 상태와 호출 예산도 모듈 전역이라 테스트 사이에 새어 나간다.

    제한을 재현한 테스트가 60초짜리 차단을 남기면 뒤 테스트의 Last.fm 호출이
    전부 건너뛰어진다. 예산도 마찬가지로, 앞 테스트가 채워 둔 창 때문에 뒤
    테스트가 이유 없이 기다린다.
    """
    sources._LASTFM_RATE_LIMIT_UNTIL = 0.0
    sources._LASTFM_SENT.clear()
    yield
    sources._LASTFM_RATE_LIMIT_UNTIL = 0.0
    sources._LASTFM_SENT.clear()
