from typing import TypeVar

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
    ) -> ResponseModelT | str:
        mime_type = "application/json" if response_schema else "text/plain"

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,  # 0~1, 0에 가까울수록 보수적/ 1에 가까울소록 창의적
            response_mime_type=mime_type,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            thinking_config=(
                types.ThinkingConfig(thinking_budget=0)
                if self.model_name.startswith("gemini-3")
                else None
            ),
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
        return raw_text
