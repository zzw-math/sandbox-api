from sandbox_api.errors import WorkspaceLimitExceededError
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_executor import ToolExecutor


async def execute_tool_call(
    manager: SandboxManager,
    executor: ToolExecutor,
    sandbox_id: str,
    request_id: str,
    tool: str,
    args: dict,
) -> dict:
    sandbox = manager.get_sandbox(sandbox_id)
    if sandbox is None:
        raise LookupError("Sandbox not found")
    if sandbox.status != "ready":
        raise RuntimeError(f"Sandbox is not ready. Current status: {sandbox.status}")

    lock = manager.get_lock(sandbox_id)

    manager.record_tool_call_start(
        request_id=request_id,
        sandbox_id=sandbox_id,
        tool=tool,
        args=args,
    )

    async with lock:
        try:
            result = await executor.execute(sandbox, tool, args)
            manager.touch_sandbox(sandbox_id)
            manager.record_tool_call_finish(
                request_id=request_id,
                status="succeeded",
                result=result,
            )
            return result
        except Exception as exc:
            manager.record_tool_call_finish(
                request_id=request_id,
                status="failed",
                error_text=str(exc),
            )
            if isinstance(exc, WorkspaceLimitExceededError):
                raise
            raise
