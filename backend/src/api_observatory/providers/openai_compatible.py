import hashlib
import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api_observatory.config import ResolvedProvider
from api_observatory.redaction import redact_headers, redact_text
from api_observatory.timing import Clock, RequestTimer, RequestTiming, SystemClock


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "developer", "user", "assistant"]
    content: str = Field(max_length=1_000_000)


class CompletionParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    seed: int | None = None
    stop: str | tuple[str, ...] | None = None


class StandardCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ChatMessage, ...] = Field(min_length=1, max_length=100)
    parameters: CompletionParameters = Field(default_factory=CompletionParameters)
    model: str | None = Field(default=None, min_length=1, max_length=200)


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class RawProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    http_status: int = Field(ge=100, le=599)
    provider_request_id: str | None
    headers: dict[str, str]
    media_type: str | None
    body_text: str
    body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes_received: int = Field(ge=0)
    credential_redacted: bool


class StandardCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_id: str
    model: str
    content: str
    finish_reason: str | None
    usage: TokenUsage | None
    raw_response: RawProviderResponse
    timing: RequestTiming


class ProviderCallError(RuntimeError):
    def __init__(
        self,
        *,
        category: Literal["connect", "timeout", "http", "provider", "protocol", "parse"],
        code: str,
        phase: Literal["request_send", "response_headers", "response_body", "protocol_decode"],
        message: str,
        retryable: bool,
        http_status: int | None = None,
        provider_code: str | None = None,
        raw_response: RawProviderResponse | None = None,
        timing: RequestTiming,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.category = category
        self.code = code
        self.phase = phase
        self.retryable = retryable
        self.http_status = http_status
        self.provider_code = provider_code
        self.raw_response = raw_response
        self.timing = timing


class _ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


class _ProviderChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _ProviderMessage
    finish_reason: str | None = None


class _ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class _ProviderCompletion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    model: str
    choices: tuple[_ProviderChoice, ...] = Field(min_length=1)
    usage: _ProviderUsage | None = None


def _request_payload(
    provider: ResolvedProvider, request: StandardCompletionRequest
) -> dict[str, object]:
    parameters = request.parameters.model_dump(exclude_none=True)
    max_output_tokens = parameters.pop("max_output_tokens", None)
    if max_output_tokens is not None:
        parameters["max_tokens"] = max_output_tokens
    return {
        "model": request.model or provider.definition.default_model,
        "messages": [message.model_dump() for message in request.messages],
        "stream": False,
        **parameters,
    }


def _raw_response(
    response: httpx.Response, api_key: str, timing: RequestTiming
) -> RawProviderResponse:
    try:
        body_text = response.content.decode("utf-8")
    except UnicodeDecodeError:
        raise ProviderCallError(
            category="parse",
            code="PARSE.NON_UTF8_RESPONSE",
            phase="response_body",
            message="Provider response body is not valid UTF-8.",
            retryable=False,
            http_status=response.status_code,
            timing=timing,
        ) from None

    received_bytes = body_text.encode("utf-8")
    stored_body_text = redact_text(body_text, [api_key])
    stored_body_bytes = stored_body_text.encode("utf-8")
    headers = redact_headers(dict(response.headers), [api_key])
    return RawProviderResponse(
        http_status=response.status_code,
        provider_request_id=headers.get("x-request-id"),
        headers=headers,
        media_type=response.headers.get("content-type"),
        body_text=stored_body_text,
        body_sha256=hashlib.sha256(stored_body_bytes).hexdigest(),
        bytes_received=len(received_bytes),
        credential_redacted=stored_body_text != body_text,
    )


def _provider_error(raw: RawProviderResponse, timing: RequestTiming) -> ProviderCallError:
    provider_code = None
    category: Literal["http", "provider"] = "http"
    try:
        document = json.loads(raw.body_text)
        error = document.get("error") if isinstance(document, dict) else None
        if isinstance(error, dict):
            category = "provider"
            code_value = error.get("code")
            provider_code = str(code_value) if code_value is not None else None
    except json.JSONDecodeError:
        pass

    retryable = raw.http_status in {408, 409, 425, 429} or raw.http_status >= 500
    return ProviderCallError(
        category=category,
        code="PROVIDER.ERROR_RESPONSE" if category == "provider" else "HTTP.NON_SUCCESS",
        phase="response_body",
        message="Provider returned a non-success HTTP response.",
        retryable=retryable,
        http_status=raw.http_status,
        provider_code=provider_code,
        raw_response=raw,
        timing=timing,
    )


class OpenAICompatibleAdapter:
    def __init__(self, client: httpx.AsyncClient, clock: Clock | None = None) -> None:
        self._client = client
        self._clock = clock or SystemClock()

    async def complete_standard(
        self,
        provider: ResolvedProvider,
        request: StandardCompletionRequest,
    ) -> StandardCompletionResult:
        api_key = provider.api_key.get_secret_value()
        headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "user-agent": "api-observatory/0.1.0",
        }
        payload = _request_payload(provider, request)
        timeout_seconds = provider.definition.timeout_ms / 1000
        timer = RequestTimer.start(self._clock)
        try:
            response = await self._client.post(
                str(provider.definition.endpoint),
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException:
            timing = timer.finish()
            raise ProviderCallError(
                category="timeout",
                code="TIMEOUT.REQUEST",
                phase="request_send",
                message="Provider request exceeded its timeout.",
                retryable=True,
                timing=timing,
            ) from None
        except httpx.RequestError:
            timing = timer.finish()
            raise ProviderCallError(
                category="connect",
                code="CONNECT.REQUEST_FAILED",
                phase="request_send",
                message="Provider request could not be completed.",
                retryable=True,
                timing=timing,
            ) from None

        timing = timer.finish()
        raw = _raw_response(response, api_key, timing)
        if not 200 <= response.status_code <= 299:
            raise _provider_error(raw, timing)

        try:
            parsed = _ProviderCompletion.model_validate_json(raw.body_text)
        except ValidationError:
            raise ProviderCallError(
                category="protocol",
                code="PROTOCOL.INVALID_COMPLETION",
                phase="protocol_decode",
                message="Provider response does not match the chat completion protocol.",
                retryable=False,
                http_status=response.status_code,
                raw_response=raw,
                timing=timing,
            ) from None

        usage = None
        if parsed.usage is not None:
            usage = TokenUsage(
                input_tokens=parsed.usage.prompt_tokens,
                output_tokens=parsed.usage.completion_tokens,
                total_tokens=parsed.usage.total_tokens,
            )
        choice = parsed.choices[0]
        return StandardCompletionResult(
            completion_id=parsed.id,
            model=parsed.model,
            content=choice.message.content,
            finish_reason=choice.finish_reason,
            usage=usage,
            raw_response=raw,
            timing=timing,
        )
