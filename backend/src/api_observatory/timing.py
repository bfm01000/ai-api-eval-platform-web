import time
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class Clock(Protocol):
    def utc_now(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...


class SystemClock:
    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.perf_counter_ns()


def _elapsed_ms(since_ns: int, now_ns: int) -> float:
    return round((now_ns - since_ns) / 1_000_000, 3)


class RequestTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_clock: Literal["monotonic"] = "monotonic"
    request_started_at: AwareDatetime
    response_completed_at: AwareDatetime
    response_completed_ms: float = Field(ge=0)
    first_content_ms: float | None = Field(default=None, ge=0)

    @field_validator("request_started_at", "response_completed_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("first_content_ms")
    @classmethod
    def first_content_not_after_completion(cls, value: float | None, info) -> float | None:
        completed = info.data.get("response_completed_ms")
        if value is not None and completed is not None and value > completed:
            raise ValueError("first_content_ms must not exceed response_completed_ms")
        return value

    @property
    def total_latency_ms(self) -> float:
        return self.response_completed_ms

    @property
    def generation_duration_ms(self) -> float | None:
        if self.first_content_ms is None:
            return None
        return round(self.response_completed_ms - self.first_content_ms, 3)


class RequestTimer:
    def __init__(
        self,
        *,
        clock: Clock,
        request_started_at: datetime,
        started_ns: int,
    ) -> None:
        self._clock = clock
        self._request_started_at = request_started_at
        self._started_ns = started_ns
        self._first_content_ms: float | None = None
        self._finished = False

    @classmethod
    def start(cls, clock: Clock) -> "RequestTimer":
        request_started_at = clock.utc_now()
        started_ns = clock.monotonic_ns()
        return cls(
            clock=clock,
            request_started_at=request_started_at,
            started_ns=started_ns,
        )

    def record_first_content(self) -> float:
        if self._finished:
            raise RuntimeError("Request timer has already been finished.")
        if self._first_content_ms is not None:
            raise RuntimeError("First content has already been recorded.")

        self._first_content_ms = _elapsed_ms(self._started_ns, self._clock.monotonic_ns())
        return self._first_content_ms

    def finish(self) -> RequestTiming:
        if self._finished:
            raise RuntimeError("Request timer has already been finished.")

        completed_ns = self._clock.monotonic_ns()
        response_completed_at = max(self._clock.utc_now(), self._request_started_at)
        elapsed_ns = completed_ns - self._started_ns
        if elapsed_ns < 0:
            raise ValueError("Monotonic clock moved backwards during the request.")

        self._finished = True
        return RequestTiming(
            request_started_at=self._request_started_at,
            response_completed_at=response_completed_at,
            response_completed_ms=_elapsed_ms(self._started_ns, completed_ns),
            first_content_ms=self._first_content_ms,
        )
