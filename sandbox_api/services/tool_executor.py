from typing import Any

from sandbox_api.config import settings
from sandbox_api.errors import WorkspaceLimitExceededError
from sandbox_api.runtime.base import Runtime
from sandbox_api.services.path_guard import resolve_safe_path
from sandbox_api.services.sandbox_manager import SandboxRecord
from sandbox_api.services.workspace_limits import (
    projected_workspace_size_bytes,
    workspace_size_bytes,
)


class ToolExecutor:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def execute(self, sandbox: SandboxRecord, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "read":
            return await self._read(sandbox, args)
        if tool == "write":
            return await self._write(sandbox, args)
        if tool == "bash":
            return await self._bash(sandbox, args)
        raise ValueError(f"Unsupported tool: {tool}")

    async def _read(self, sandbox: SandboxRecord, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        if not isinstance(path, str):
            raise ValueError("read.args.path must be a string")

        target = resolve_safe_path(sandbox.workspace_path, path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not target.is_file():
            raise ValueError(f"Path is not a file: {path}")

        content = target.read_text(encoding="utf-8")
        return {
            "path": path,
            "content": content,
        }

    async def _write(self, sandbox: SandboxRecord, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        content = args.get("content", "")
        mode = args.get("mode", "overwrite")

        if not isinstance(path, str):
            raise ValueError("write.args.path must be a string")
        if not isinstance(content, str):
            raise ValueError("write.args.content must be a string")
        if mode not in {"overwrite", "append"}:
            raise ValueError("write.args.mode must be 'overwrite' or 'append'")

        target = resolve_safe_path(sandbox.workspace_path, path)
        target.parent.mkdir(parents=True, exist_ok=True)

        content_size = len(content.encode("utf-8"))
        projected_size = projected_workspace_size_bytes(
            sandbox.workspace_path,
            target,
            content_size,
            mode,
        )
        if projected_size > settings.workspace_soft_limit_bytes:
            raise WorkspaceLimitExceededError(
                "Workspace soft limit exceeded by write: "
                f"{projected_size} > {settings.workspace_soft_limit_bytes} bytes"
            )

        write_mode = "a" if mode == "append" else "w"
        with target.open(write_mode, encoding="utf-8") as file:
            file.write(content)

        return {
            "path": path,
            "bytesWritten": content_size,
            "mode": mode,
            "workspaceUsageBytes": workspace_size_bytes(sandbox.workspace_path),
            "workspaceLimitBytes": settings.workspace_soft_limit_bytes,
        }

    async def _bash(self, sandbox: SandboxRecord, args: dict[str, Any]) -> dict[str, Any]:
        command = args.get("command")
        timeout_ms = args.get("timeoutMs", 30_000)
        env = args.get("env")

        if not isinstance(command, str) or not command.strip():
            raise ValueError("bash.args.command must be a non-empty string")
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("bash.args.timeoutMs must be a positive integer")
        if env is not None and not isinstance(env, dict):
            raise ValueError("bash.args.env must be an object if provided")

        normalized_env = None
        if env is not None:
            normalized_env = {}
            for key, value in env.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise ValueError("bash.args.env keys and values must be strings")
                normalized_env[key] = value

        current_workspace_size = workspace_size_bytes(sandbox.workspace_path)
        if current_workspace_size > settings.workspace_soft_limit_bytes:
            raise WorkspaceLimitExceededError(
                "Workspace soft limit already exceeded before bash: "
                f"{current_workspace_size} > {settings.workspace_soft_limit_bytes} bytes"
            )

        result = await self.runtime.run_bash(
            sandbox_id=sandbox.sandbox_id,
            workspace=sandbox.workspace_path,
            command=command,
            timeout_ms=timeout_ms,
            env=normalized_env,
        )
        result["workspaceUsageBytes"] = workspace_size_bytes(sandbox.workspace_path)
        result["workspaceLimitBytes"] = settings.workspace_soft_limit_bytes
        result["workspaceLimitExceeded"] = (
            result["workspaceUsageBytes"] > settings.workspace_soft_limit_bytes
        )
        return result
