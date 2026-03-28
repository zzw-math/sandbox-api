import os
import re
import uuid
from collections.abc import Callable
from typing import Any

from fastmcp import Context, FastMCP

from sandbox_api.config import settings
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_call_service import execute_tool_call
from sandbox_api.services.tool_executor import ToolExecutor


class RemoteSandboxMcpBridge:
    def __init__(
        self,
        manager_provider: Callable[[], SandboxManager],
        executor_provider: Callable[[], ToolExecutor],
    ) -> None:
        self._manager_provider = manager_provider
        self._executor_provider = executor_provider
        self._session_sandboxes: dict[str, str] = {}
        self._fixed_sandbox_id = os.getenv("SANDBOX_MCP_SANDBOX_ID")
        self._tenant_id = os.getenv("SANDBOX_MCP_TENANT_ID", settings.default_tenant_id)

        self.server = FastMCP(
            name="sandbox-api-mcp",
            instructions=(
                "Use read, write, and bash to interact with a sandboxed workspace. "
                "The sandbox is selected automatically per MCP session."
            ),
        )
        self._register_tools()

    def http_app(self):
        return self.server.http_app(path="/", transport="streamable-http")

    def _register_tools(self) -> None:
        @self.server.tool(
            name="read",
            title="Read File",
            description="Read a UTF-8 text file from the current sandbox workspace.",
        )
        async def read(path: str, ctx: Context) -> dict[str, Any]:
            return await self._call_tool(ctx, "read", {"path": path})

        @self.server.tool(
            name="write",
            title="Write File",
            description="Write UTF-8 text content into the current sandbox workspace.",
        )
        async def write(
            path: str,
            content: str,
            mode: str = "overwrite",
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            if ctx is None:
                raise RuntimeError("FastMCP context is required")
            return await self._call_tool(
                ctx,
                "write",
                {
                    "path": path,
                    "content": content,
                    "mode": mode,
                },
            )

        @self.server.tool(
            name="bash",
            title="Run Bash",
            description="Run a shell command inside the current sandbox container.",
        )
        async def bash(
            command: str,
            timeoutMs: int = 30_000,
            env: dict[str, str] | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            if ctx is None:
                raise RuntimeError("FastMCP context is required")
            return await self._call_tool(
                ctx,
                "bash",
                {
                    "command": command,
                    "timeoutMs": timeoutMs,
                    "env": env,
                },
            )

    async def _call_tool(self, ctx: Context, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        sandbox_id = await self._ensure_sandbox(ctx)
        request_id = self._build_request_id(ctx.session_id, tool_name)
        result = await execute_tool_call(
            manager=self._manager_provider(),
            executor=self._executor_provider(),
            sandbox_id=sandbox_id,
            request_id=request_id,
            tool=tool_name,
            args=args,
        )
        return {
            "sandboxId": sandbox_id,
            "requestId": request_id,
            "tool": tool_name,
            "result": result,
        }

    async def _ensure_sandbox(self, ctx: Context) -> str:
        manager = self._manager_provider()
        if self._fixed_sandbox_id is not None:
            sandbox = manager.get_sandbox(self._fixed_sandbox_id)
            if sandbox is None:
                raise LookupError(f"Configured sandbox not found: {self._fixed_sandbox_id}")
            if sandbox.status != "ready":
                await manager.resume_sandbox(self._fixed_sandbox_id)
            return self._fixed_sandbox_id

        session_id = str(ctx.session_id)
        sandbox_id = self._session_sandboxes.get(session_id)
        if sandbox_id is not None:
            sandbox = manager.get_sandbox(sandbox_id)
            if sandbox is None:
                self._session_sandboxes.pop(session_id, None)
            else:
                if sandbox.status != "ready":
                    await manager.resume_sandbox(sandbox_id)
                return sandbox_id

        metadata = {
            "source": "mcp-http",
            "mcpSessionId": session_id,
        }
        record = await manager.create_sandbox(
            tenant_id=self._tenant_id,
            metadata=metadata,
        )
        self._session_sandboxes[session_id] = record.sandbox_id
        return record.sandbox_id

    def _build_request_id(self, session_id: str, tool_name: str) -> str:
        safe_session = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
        suffix = uuid.uuid4().hex[:8]
        return f"mcp_{safe_session}_{tool_name}_{suffix}"
