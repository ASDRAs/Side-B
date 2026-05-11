import pytest

import app.pipeline.parser as _parser_module


@pytest.fixture(autouse=True)
def clear_parse_cache():
    _parser_module._PARSE_CACHE.clear()
    yield
    _parser_module._PARSE_CACHE.clear()
