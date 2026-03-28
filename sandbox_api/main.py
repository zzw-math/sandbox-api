from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from sandbox_api.db import init_db
from sandbox_api.errors import CapacityExceededError, WorkspaceLimitExceededError
from sandbox_api.runtime.docker import DockerRuntime
from sandbox_api.schemas import (
    CreateSandboxRequest,
    SandboxResponse,
    ToolCallRequest,
    ToolCallResponse,
)
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_executor import ToolExecutor


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Sandbox API MVP",
    version="0.1.0",
    lifespan=lifespan,
)

runtime = DockerRuntime()
manager = SandboxManager(runtime=runtime)
executor = ToolExecutor(runtime=runtime)


def to_sandbox_response(record) -> SandboxResponse:
    return SandboxResponse(
        sandboxId=record.sandbox_id,
        tenantId=record.tenant_id,
        status=record.status,
        workspacePath=str(record.workspace_path),
        createdAt=record.created_at,
        lastActiveAt=record.last_active_at,
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/sandboxes", response_model=SandboxResponse)
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


@app.get("/v1/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str) -> SandboxResponse:
    record = manager.get_sandbox(sandbox_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return to_sandbox_response(record)


@app.post("/v1/sandboxes/{sandbox_id}/resume", response_model=SandboxResponse)
async def resume_sandbox(sandbox_id: str) -> SandboxResponse:
    try:
        record = await manager.resume_sandbox(sandbox_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return to_sandbox_response(record)


@app.delete("/v1/sandboxes/{sandbox_id}")
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


@app.post("/v1/tool-call", response_model=ToolCallResponse)
async def tool_call(request: ToolCallRequest) -> ToolCallResponse:
    sandbox = manager.get_sandbox(request.sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    if sandbox.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Sandbox is not ready. Current status: {sandbox.status}",
        )

    lock = manager.get_lock(request.sandbox_id)

    try:
        manager.record_tool_call_start(
            request_id=request.request_id,
            sandbox_id=request.sandbox_id,
            tool=request.tool,
            args=request.args,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"requestId conflict or invalid state: {exc}") from exc

    async with lock:
        try:
            result = await executor.execute(sandbox, request.tool, request.args)
            manager.touch_sandbox(request.sandbox_id)
            manager.record_tool_call_finish(
                request_id=request.request_id,
                status="succeeded",
                result=result,
            )
            return ToolCallResponse(
                requestId=request.request_id,
                sandboxId=request.sandbox_id,
                ok=True,
                result=result,
            )
        except Exception as exc:
            manager.record_tool_call_finish(
                request_id=request.request_id,
                status="failed",
                error_text=str(exc),
            )
            status_code = 400
            if isinstance(exc, WorkspaceLimitExceededError):
                status_code = 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
