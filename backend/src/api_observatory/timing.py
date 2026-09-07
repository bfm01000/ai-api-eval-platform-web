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


class RequestTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_clock: Literal["monotonic"] = "monotonic"
    request_started_at: AwareDatetime
    response_completed_at: AwareDatetime
    response_completed_ms: float = Field(ge=0)

    @field_validator("request_started_at", "response_completed_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @property
    def total_latency_ms(self) -> float:
        return self.response_completed_ms


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
            response_completed_ms=round(elapsed_ns / 1_000_000, 3),
        )
