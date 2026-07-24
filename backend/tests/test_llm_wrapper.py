from app.llm.llm_response import OppositeTagAnalysis
from app.llm.llm_wrapper import GeminiWrapper


class FakeResponse:
    def __init__(self, *, parsed, text):
        self.parsed = parsed
        self.text = text


class FakeModels:
    def __init__(self, response):
        self.response = response

    def generate_content(self, **kwargs):
        return self.response


class FakeClient:
    def __init__(self, response):
        self.models = FakeModels(response)


def wrapper_with_response(response):
    wrapper = GeminiWrapper.__new__(GeminiWrapper)
    wrapper.model_name = "fake-model"
    wrapper.client = FakeClient(response)
    return wrapper


def test_request_prefers_structured_response_over_non_json_text():
    wrapper = wrapper_with_response(
        FakeResponse(
            parsed={"opposite_tags": ["bright", "energetic"]},
            text="Here is the JSON requested:\n```json\n{}\n```",
        )
    )

    result = wrapper.request(
        system_prompt="system",
        user_prompt="user",
        response_schema=OppositeTagAnalysis,
        response_validator=OppositeTagAnalysis,
    )

    assert result == OppositeTagAnalysis(opposite_tags=["bright", "energetic"])


def test_request_falls_back_to_raw_json_when_parsed_response_is_missing():
    wrapper = wrapper_with_response(
        FakeResponse(
            parsed=None,
            text='{"opposite_tags":["calm"]}',
        )
    )

    result = wrapper.request(
        system_prompt="system",
        user_prompt="user",
        response_schema=OppositeTagAnalysis,
        response_validator=OppositeTagAnalysis,
    )

    assert result == OppositeTagAnalysis(opposite_tags=["calm"])
