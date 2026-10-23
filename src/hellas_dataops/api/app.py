"""Application factory: no configuration or database I/O during import."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from hellas_dataops.api.routes.read import router
from hellas_dataops.storage.queries import QueryNotFound, QueryUnavailable, ReadQueries
from hellas_dataops.storage.test_database import TestDatabaseConfig


def create_app(target: TestDatabaseConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="Hellas DataOps",
        version="0.1.0",
        description="Read-only stored weather. Local synthetic demonstration; no live retrieval.",
    )
    app.state.queries = ReadQueries(target)
    app.include_router(router)

    @app.exception_handler(QueryUnavailable)
    async def unavailable(request: Request, error: QueryUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": "Database unavailable or incompatible"}
        )

    @app.exception_handler(QueryNotFound)
    async def not_found(request: Request, error: QueryNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": "Unknown city or unpersisted configuration/dataset"},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        # Do not echo arbitrary query inputs or exception context.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Invalid query: use documented identities, UTC hours, a nonempty span up to 31 days and limit 1-500"
            },
        )

    return app
