from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from api_observatory import __version__


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: str
    version: str
    contract_versions: tuple[str, ...]


def create_app() -> FastAPI:
    application = FastAPI(
        title="AI API Observatory",
        summary="Reproducible AI API service quality evaluation.",
        version=__version__,
    )

    @application.get("/health", tags=["system"], response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="api-observatory-backend",
            version=__version__,
            contract_versions=("performance-request.v1", "performance-result.v1"),
        )

    return application


app = create_app()
