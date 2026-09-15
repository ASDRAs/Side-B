import time

import pytest

from app.services import catalog
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
    """차단 상태와 토큰도 모듈 전역이라 테스트 사이에 새어 나간다.

    제한을 재현한 테스트가 60초짜리 차단을 남기면 뒤 테스트의 Last.fm 호출이
    전부 건너뛰어진다. 토큰도 마찬가지로, 앞 테스트가 비워 두면 뒤 테스트가
    이유 없이 기다린다.
    """
    _reset_lastfm_state()
    yield
    _reset_lastfm_state()


@pytest.fixture(autouse=True)
def _reset_provider_gates():
    """iTunes·Deezer 게이트도 모듈 전역이다.

    429를 재현한 테스트가 차단기를 열어 두면 뒤 테스트의 공급자 호출이 전부
    건너뛰어진다. 세마포어는 처음 대기가 생긴 이벤트 루프에 묶이므로 루프가
    바뀌는 테스트마다 새로 만든다.
    """
    catalog.ITUNES.reset()
    catalog.DEEZER.reset()
    yield
    catalog.ITUNES.reset()
    catalog.DEEZER.reset()


def _reset_lastfm_state():
    sources._LASTFM_RATE_LIMIT_UNTIL = 0.0
    sources._LASTFM_TOKENS = sources._LASTFM_BURST
    sources._LASTFM_TOKENS_UPDATED = time.monotonic()
