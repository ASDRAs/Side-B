import asyncio
import time
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class InferenceConfigurationError(Exception):
    pass


class InferenceUnavailableError(Exception):
    pass


class InferenceTimeoutError(Exception):
    pass


class InferenceAudioError(Exception):
    pass


class InferencePrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genre: str = Field(min_length=1)
    score: float = Field(allow_inf_nan=False)
    model_version: str = Field(min_length=1)


class InferenceClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        audience: str = "",
        use_iam: bool = True,
        timeout: float = 90,
    ):
        self.http = http
        self.url = url.strip().rstrip("/")
        # Tagged revision URLs route requests; IAM still expects the service origin.
        self.audience = audience.strip().rstrip("/") or self.url
        self.use_iam = use_iam
        self.timeout = timeout
        self._token = ""
        self._token_until = 0.0
        self._token_lock = asyncio.Lock()
        for origin in {self.url, self.audience} - {""}:
            parsed = urlsplit(origin)
            local = parsed.hostname in {
                "localhost",
                "127.0.0.1",
                "::1",
                "host.docker.internal",
            }
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
                or parsed.scheme not in {"https", "http"}
                or (parsed.scheme != "https" and not local)
                or (not use_iam and not local)
            ):
                raise InferenceConfigurationError(
                    "Inference URL must be an HTTPS service origin (unauthenticated HTTP is local-only)"
                )

    async def _identity_token(self):
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_until:
                return self._token
            try:
                response = await self.http.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
                    params={"audience": self.audience, "format": "full"},
                    headers={"Metadata-Flavor": "Google"},
                    timeout=3,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise InferenceUnavailableError("Service identity unavailable") from exc
            if not response.text.strip() or len(response.content) > 16384:
                raise InferenceUnavailableError("Invalid service identity response")
            self._token = response.text.strip()
            self._token_until = time.monotonic() + 3000
            return self._token

    async def predict(self, audio: bytes) -> InferencePrediction:
        if not self.url:
            raise InferenceConfigurationError("CLAP_INFERENCE_URL is not configured")
        headers = {"Content-Type": "application/octet-stream"}
        if self.use_iam:
            headers["Authorization"] = "Bearer " + await self._identity_token()
        try:
            response = await self.http.post(
                self.url + "/predict",
                content=audio,
                headers=headers,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError("Inference timed out") from exc
        except httpx.HTTPError as exc:
            raise InferenceUnavailableError("Inference unavailable") from exc
        if response.status_code in {400, 413, 415, 422}:
            raise InferenceAudioError("Preview audio could not be analyzed")
        if response.status_code in {401, 403}:
            self._token_until = 0
        if response.status_code != 200:
            raise InferenceUnavailableError("Inference unavailable")
        try:
            return InferencePrediction.model_validate_json(response.content)
        except ValidationError as exc:
            raise InferenceUnavailableError("Invalid inference response") from exc
