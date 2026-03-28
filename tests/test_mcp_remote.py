import asyncio

from fastmcp import Client

from sandbox_api.mcp.remote import RemoteSandboxMcpBridge


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
