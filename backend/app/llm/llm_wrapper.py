from typing import Any, Callable, Optional, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class GeminiWrapper:
    def __init__(self, api_key, model_name):
        self.api_key = api_key
        self.model_name = model_name
        self.client = genai.Client(api_key=self.api_key)

    def request(
        self,
        system_prompt,
        user_prompt,
        temperature: float = 0.7,
        max_output_tokens: int = 300,
        response_schema=None,
        response_validator: type[ResponseModelT] | None = None,
        postprocess_func: Optional[Callable[[str], Any]] = None,
    ) -> ResponseModelT | str:
        mime_type = "application/json" if response_schema else "text/plain"

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,  # 0~1, 0에 가까울수록 보수적/ 1에 가까울소록 창의적
            response_mime_type=mime_type,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
        )
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=config,
        )
        raw_text = response.text

        if response_validator:
            parsed = response.parsed
            if parsed is not None:
                if isinstance(parsed, response_validator):
                    return parsed
                return response_validator.model_validate(parsed)
            return response_validator.model_validate_json(raw_text)
        if postprocess_func:
            return postprocess_func(raw_text)
        return raw_text


# TODO
# class GeminiInteractionWrapper:
#     # interaction은 구글 검색 등등 기능 사용 가능/chat 방식(대화 log가 기억됨)
#     def __init__(self, api_key=None, model_name="gemini-3.5-flash"):
#         self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
#         self.model_name = model_name or os.environ.get("GEMINI_MODEL")
#         if not self.api_key:
#             raise ValueError(
#                 "Gemini API 키가 필요합니다. 환경변수에 등록하거나 직접 입력해주세요."
#             )

#         self.client = genai.Client(api_key=self.api_key)
