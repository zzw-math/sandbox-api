import json
import os
import re
import sys
import uuid
from typing import Any

from sandbox_api.config import settings
from sandbox_api.errors import WorkspaceLimitExceededError
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_call_service import execute_tool_call
from sandbox_api.services.tool_executor import ToolExecutor

MCP_PROTOCOL_VERSION = "2025-06-18"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class SandboxMcpServer:
    def __init__(self, manager: SandboxManager, executor: ToolExecutor) -> None:
        self.manager = manager
        self.executor = executor
        self.protocol_version = MCP_PROTOCOL_VERSION
        self.client_info: dict[str, Any] | None = None
        self.initialized = False
        self._session_token = uuid.uuid4().hex[:12]
        self._sandbox_id: str | None = os.getenv("SANDBOX_MCP_SANDBOX_ID")
        self._tenant_id = os.getenv("SANDBOX_MCP_TENANT_ID", settings.default_tenant_id)

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if "jsonrpc" not in message or message["jsonrpc"] != "2.0":
            raise JsonRpcError(-32600, "Invalid Request")

        method = message.get("method")
        if not isinstance(method, str):
            raise JsonRpcError(-32600, "Invalid Request")

        params = message.get("params", {})
        request_id = message.get("id")

        if method == "notifications/initialized":
            self.initialized = True
            return None

        if request_id is None:
            return None

        if method == "initialize":
            if not isinstance(params, dict):
                raise JsonRpcError(-32602, "Invalid params")
            self.client_info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else None
            self.initialized = True
            return self._success(
                request_id,
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {
                        "tools": {
                            "listChanged": False,
                        }
                    },
                    "serverInfo": {
                        "name": "sandbox-api-mcp",
                        "version": "0.1.0",
                    },
                    "instructions": (
                        "This server exposes read, write, and bash tools backed by a per-session sandbox. "
                        "A sandbox is created lazily on the first tool call."
                    ),
                },
            )

        self._ensure_initialized()

        if method == "ping":
            return self._success(request_id, {})
        if method == "tools/list":
            return self._success(request_id, {"tools": self._tool_definitions()})
        if method == "prompts/list":
            return self._success(request_id, {"prompts": []})
        if method == "resources/list":
            return self._success(request_id, {"resources": []})
        if method == "tools/call":
            if not isinstance(params, dict):
                raise JsonRpcError(-32602, "Invalid params")
            return self._success(request_id, await self._handle_tools_call(request_id, params))

        raise JsonRpcError(-32601, f"Method not found: {method}")

    async def shutdown(self) -> None:
        sandbox_id = self._sandbox_id
        if sandbox_id is None:
            return

        stop_on_exit = os.getenv("SANDBOX_MCP_STOP_ON_EXIT", "true").lower() != "false"
        if not stop_on_exit:
            return

        sandbox = self.manager.get_sandbox(sandbox_id)
        if sandbox is None or sandbox.status != "ready":
            return
        try:
            await self.manager.delete_sandbox(sandbox_id, purge=False)
        except Exception as exc:
            print(f"[sandbox-api-mcp] failed to stop sandbox {sandbox_id}: {exc}", file=sys.stderr)

    async def _handle_tools_call(self, rpc_request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if not isinstance(tool_name, str):
            raise JsonRpcError(-32602, "tools/call.params.name must be a string")
        if tool_name not in {"read", "write", "bash"}:
            raise JsonRpcError(-32601, f"Unknown tool: {tool_name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(-32602, "tools/call.params.arguments must be an object")

        sandbox_id = await self._ensure_sandbox()
        generated_request_id = self._build_request_id(tool_name, rpc_request_id)

        try:
            result = await execute_tool_call(
                manager=self.manager,
                executor=self.executor,
                sandbox_id=sandbox_id,
                request_id=generated_request_id,
                tool=tool_name,
                args=arguments,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=True, indent=2),
                    }
                ],
                "structuredContent": result,
                "_meta": {
                    "sandboxId": sandbox_id,
                    "requestId": generated_request_id,
                },
            }
        except LookupError as exc:
            raise JsonRpcError(-32001, str(exc)) from exc
        except RuntimeError as exc:
            return self._tool_error(str(exc), sandbox_id, generated_request_id)
        except WorkspaceLimitExceededError as exc:
            return self._tool_error(str(exc), sandbox_id, generated_request_id)
        except Exception as exc:
            return self._tool_error(str(exc), sandbox_id, generated_request_id)

    async def _ensure_sandbox(self) -> str:
        if self._sandbox_id is not None:
            sandbox = self.manager.get_sandbox(self._sandbox_id)
            if sandbox is None:
                raise JsonRpcError(-32001, f"Configured sandbox not found: {self._sandbox_id}")
            if sandbox.status != "ready":
                await self.manager.resume_sandbox(self._sandbox_id)
            return self._sandbox_id

        metadata = {
            "source": "mcp",
            "mcpSessionId": self._session_token,
        }
        if self.client_info is not None:
            metadata["client"] = self.client_info

        record = await self.manager.create_sandbox(
            tenant_id=self._tenant_id,
            metadata=metadata,
        )
        self._sandbox_id = record.sandbox_id
        print(
            f"[sandbox-api-mcp] created sandbox {record.sandbox_id} for session {self._session_token}",
            file=sys.stderr,
        )
        return record.sandbox_id

    def _build_request_id(self, tool_name: str, rpc_request_id: Any) -> str:
        if rpc_request_id is None:
            rpc_fragment = uuid.uuid4().hex[:8]
        else:
            rpc_fragment = str(rpc_request_id)
        rpc_fragment = re.sub(r"[^A-Za-z0-9_-]", "_", rpc_fragment)
        return f"mcp_{self._session_token}_{tool_name}_{rpc_fragment}"

    def _ensure_initialized(self) -> None:
        if self.initialized:
            return
        if self.client_info is not None:
            return
        raise JsonRpcError(-32002, "Server not initialized")

    def _success(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    def _tool_error(self, message: str, sandbox_id: str, request_id: str) -> dict[str, Any]:
        payload = {
            "error": message,
            "sandboxId": sandbox_id,
            "requestId": request_id,
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=True, indent=2),
                }
            ],
            "structuredContent": payload,
            "isError": True,
            "_meta": {
                "sandboxId": sandbox_id,
                "requestId": request_id,
            },
        }

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "read",
                "title": "Read File",
                "description": "Read a UTF-8 text file from the current sandbox workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the sandbox workspace.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "write",
                "title": "Write File",
                "description": "Write UTF-8 text content into the current sandbox workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the sandbox workspace.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Text content to write.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["overwrite", "append"],
                            "description": "Whether to overwrite or append to the file.",
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "bash",
                "title": "Run Bash",
                "description": "Run a shell command inside the current sandbox container.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to run.",
                        },
                        "timeoutMs": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Maximum command runtime in milliseconds.",
                        },
                        "env": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "string",
                            },
                            "description": "Optional environment variables.",
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        ]


def build_error_response(request_id: Any, error: JsonRpcError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
    }
    if error.data is not None:
        payload["data"] = error.data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": payload,
    }
