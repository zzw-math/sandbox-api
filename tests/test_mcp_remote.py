import asyncio
from pathlib import Path

from fastmcp import Client

from sandbox_api.mcp.remote import RemoteSandboxMcpBridge
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_executor import ToolExecutor


class FakeHeaders(dict):
    def get(self, key, default=None):
        for existing_key, value in self.items():
            if existing_key.lower() == key.lower():
                return value
        return default


class FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = FakeHeaders(headers or {})


class FakeRequestContext:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.request = FakeRequest(headers=headers)


class FakeContext:
    def __init__(self, session_id: str, headers: dict[str, str] | None = None) -> None:
        self.session_id = session_id
        self.request_context = FakeRequestContext(headers=headers)


class FailingRuntime:
    async def ensure_sandbox(self, sandbox_id: str, workspace: Path) -> str:
        workspace.mkdir(parents=True, exist_ok=True)
        return f"sandbox-{sandbox_id}"

    async def stop_sandbox(self, sandbox_id: str) -> None:
        return None

    async def purge_sandbox(self, sandbox_id: str) -> None:
        return None

    async def run_bash(
        self,
        sandbox_id: str,
        workspace: Path,
        command: str,
        timeout_ms: int,
        env: dict[str, str] | None = None,
    ) -> dict:
        raise RuntimeError(
            "W: Failed to fetch http://ports.ubuntu.com/ubuntu-ports/dists/noble/InRelease  "
            "Temporary failure resolving 'ports.ubuntu.com'"
        )


def test_remote_mcp_lists_tools(app_env):
    bridge = RemoteSandboxMcpBridge(
        manager_provider=lambda: app_env["manager"],
        executor_provider=lambda: app_env["executor"],
    )

    async def scenario():
        async with Client(bridge.server) as client:
            tools = await client.list_tools()
            return [tool.name for tool in tools]

    tool_names = asyncio.run(scenario())
    assert tool_names == ["read", "write", "bash"]


def test_remote_mcp_reuses_sandbox_per_session(app_env):
    bridge = RemoteSandboxMcpBridge(
        manager_provider=lambda: app_env["manager"],
        executor_provider=lambda: app_env["executor"],
    )

    async def scenario():
        async with Client(bridge.server) as client:
            write_result = await client.call_tool(
                "write",
                {
                    "path": "notes/hello.txt",
                    "content": "hello over remote mcp",
                },
            )
            read_result = await client.call_tool(
                "read",
                {
                    "path": "notes/hello.txt",
                },
            )
            return write_result.data, read_result.data

    write_data, read_data = asyncio.run(scenario())
    assert write_data["sandboxId"] == read_data["sandboxId"]
    assert read_data["result"]["content"] == "hello over remote mcp"


def test_remote_mcp_bash_uses_existing_executor(app_env):
    bridge = RemoteSandboxMcpBridge(
        manager_provider=lambda: app_env["manager"],
        executor_provider=lambda: app_env["executor"],
    )

    async def scenario():
        async with Client(bridge.server) as client:
            result = await client.call_tool(
                "bash",
                {
                    "command": "echo hi",
                    "timeoutMs": 1000,
                },
            )
            return result.data

    data = asyncio.run(scenario())
    assert data["result"]["stdout"] == "ran:echo hi"
    assert data["tool"] == "bash"


def test_remote_mcp_bash_errors_return_structured_json(app_env, monkeypatch):
    manager = SandboxManager(runtime=FailingRuntime())
    executor = ToolExecutor(runtime=FailingRuntime())
    bridge = RemoteSandboxMcpBridge(
        manager_provider=lambda: manager,
        executor_provider=lambda: executor,
    )

    record = asyncio.run(
        manager.create_sandbox(
            tenant_id="tenant-demo",
            metadata={"source": "test"},
        )
    )
    ctx = FakeContext(
        session_id="session-a",
        headers={"X-Sandbox-Id": record.sandbox_id},
    )

    payload = asyncio.run(
        bridge._call_tool(
            ctx,
            "bash",
            {
                "command": "apt-get update",
                "timeoutMs": 1000,
            },
        )
    )

    assert payload["ok"] is False
    assert payload["sandboxId"] == record.sandbox_id
    assert payload["tool"] == "bash"
    assert "Failed to fetch" in payload["error"]


def test_remote_mcp_header_selected_sandbox_overrides_session(app_env):
    manager = app_env["manager"]
    bridge = RemoteSandboxMcpBridge(
        manager_provider=lambda: app_env["manager"],
        executor_provider=lambda: app_env["executor"],
    )

    first = asyncio.run(
        manager.create_sandbox(
            tenant_id="tenant-a",
            metadata={"source": "test"},
        )
    )
    second = asyncio.run(
        manager.create_sandbox(
            tenant_id="tenant-b",
            metadata={"source": "test"},
        )
    )

    first_ctx = FakeContext("session-one", {"X-Sandbox-Id": first.sandbox_id})
    second_ctx = FakeContext("session-two", {"X-Sandbox-API-Sandbox-Id": second.sandbox_id})

    first_result = asyncio.run(
        bridge._call_tool(
            first_ctx,
            "write",
            {"path": "notes/a.txt", "content": "alpha"},
        )
    )
    second_result = asyncio.run(
        bridge._call_tool(
            second_ctx,
            "write",
            {"path": "notes/b.txt", "content": "beta"},
        )
    )

    assert first_result["ok"] is True
    assert first_result["sandboxId"] == first.sandbox_id
    assert second_result["ok"] is True
    assert second_result["sandboxId"] == second.sandbox_id
