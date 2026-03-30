from contextlib import asynccontextmanager

from fastmcp.utilities.lifespan import combine_lifespans
from fastapi import APIRouter, FastAPI, HTTPException, Query

from sandbox_api.db import init_db
from sandbox_api.errors import CapacityExceededError, WorkspaceLimitExceededError
from sandbox_api.mcp.remote import RemoteSandboxMcpBridge
from sandbox_api.runtime.docker import DockerRuntime
from sandbox_api.schemas import (
    CreateSandboxRequest,
    SandboxResponse,
    ToolCallRequest,
    ToolCallResponse,
)
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_call_service import execute_tool_call
from sandbox_api.services.tool_executor import ToolExecutor


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    init_db()
    yield


runtime = DockerRuntime()
manager = SandboxManager(runtime=runtime)
executor = ToolExecutor(runtime=runtime)
router = APIRouter()


def create_app() -> FastAPI:
    mcp_bridge = RemoteSandboxMcpBridge(
        manager_provider=lambda: manager,
        executor_provider=lambda: executor,
    )
    mcp_http_app = mcp_bridge.http_app()

    application = FastAPI(
        title="Sandbox API MVP",
        version="0.1.0",
        lifespan=combine_lifespans(app_lifespan, mcp_http_app.lifespan),
    )
    application.include_router(router)
    application.mount("/mcp", mcp_http_app)
    return application


def to_sandbox_response(record) -> SandboxResponse:
    return SandboxResponse(
        sandboxId=record.sandbox_id,
        tenantId=record.tenant_id,
        status=record.status,
        workspacePath=str(record.workspace_path),
        createdAt=record.created_at,
        lastActiveAt=record.last_active_at,
    )


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/sandboxes", response_model=SandboxResponse)
async def create_sandbox(request: CreateSandboxRequest) -> SandboxResponse:
    try:
        record = await manager.create_sandbox(
            tenant_id=request.tenant_id,
            metadata=request.metadata,
        )
    except CapacityExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return to_sandbox_response(record)


@router.get("/v1/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str) -> SandboxResponse:
    record = manager.get_sandbox(sandbox_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return to_sandbox_response(record)


@router.post("/v1/sandboxes/{sandbox_id}/resume", response_model=SandboxResponse)
async def resume_sandbox(sandbox_id: str) -> SandboxResponse:
    try:
        record = await manager.resume_sandbox(sandbox_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return to_sandbox_response(record)


@router.delete("/v1/sandboxes/{sandbox_id}")
async def delete_sandbox(
    sandbox_id: str,
    purge: bool = Query(default=False),
) -> dict[str, bool | str]:
    try:
        await manager.delete_sandbox(sandbox_id, purge=purge)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "sandboxId": sandbox_id, "purged": purge}


@router.post("/v1/tool-call", response_model=ToolCallResponse)
async def tool_call(request: ToolCallRequest) -> ToolCallResponse:
    try:
        result = await execute_tool_call(
            manager=manager,
            executor=executor,
            sandbox_id=request.sandbox_id,
            request_id=request.request_id,
            tool=request.tool,
            args=request.args,
        )
        return ToolCallResponse(
            requestId=request.request_id,
            sandboxId=request.sandbox_id,
            ok=True,
            result=result,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        status_code = 400
        if isinstance(exc, WorkspaceLimitExceededError):
            status_code = 409
        elif "UNIQUE constraint failed: tool_calls.request_id" in str(exc):
            status_code = 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


app = create_app()
