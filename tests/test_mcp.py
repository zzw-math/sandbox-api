import asyncio

from sandbox_api.mcp.protocol import SandboxMcpServer


def test_mcp_initialize_and_tools_list(app_env):
    server = SandboxMcpServer(
        manager=app_env["manager"],
        executor=app_env["executor"],
    )

    initialize_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            }
        )
    )
    assert initialize_response is not None
    assert initialize_response["result"]["serverInfo"]["name"] == "sandbox-api-mcp"

    tools_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
    )
    assert tools_response is not None
    tool_names = [tool["name"] for tool in tools_response["result"]["tools"]]
    assert tool_names == ["read", "write", "bash"]


def test_mcp_write_and_read_roundtrip(app_env):
    server = SandboxMcpServer(
        manager=app_env["manager"],
        executor=app_env["executor"],
    )

    asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            }
        )
    )

    write_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "write-1",
                "method": "tools/call",
                "params": {
                    "name": "write",
                    "arguments": {
                        "path": "notes/hello.txt",
                        "content": "hello from mcp",
                    },
                },
            }
        )
    )
    assert write_response is not None
    assert write_response["result"]["structuredContent"]["bytesWritten"] > 0
    sandbox_id = write_response["result"]["_meta"]["sandboxId"]
    assert sandbox_id.startswith("sbx_")

    read_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "read-1",
                "method": "tools/call",
                "params": {
                    "name": "read",
                    "arguments": {
                        "path": "notes/hello.txt",
                    },
                },
            }
        )
    )
    assert read_response is not None
    assert read_response["result"]["structuredContent"]["content"] == "hello from mcp"
    assert read_response["result"]["_meta"]["sandboxId"] == sandbox_id


def test_mcp_uses_configured_sandbox_id(app_env, monkeypatch):
    manager = app_env["manager"]
    executor = app_env["executor"]
    record = asyncio.run(
        manager.create_sandbox(
            tenant_id="tenant-demo",
            metadata={"source": "test"},
        )
    )
    asyncio.run(manager.delete_sandbox(record.sandbox_id, purge=False))

    monkeypatch.setenv("SANDBOX_MCP_SANDBOX_ID", record.sandbox_id)
    server = SandboxMcpServer(manager=manager, executor=executor)

    asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            }
        )
    )

    bash_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "bash-1",
                "method": "tools/call",
                "params": {
                    "name": "bash",
                    "arguments": {
                        "command": "echo hi",
                        "timeoutMs": 1000,
                    },
                },
            }
        )
    )
    assert bash_response is not None
    assert bash_response["result"]["structuredContent"]["stdout"] == "ran:echo hi"
    assert bash_response["result"]["_meta"]["sandboxId"] == record.sandbox_id
