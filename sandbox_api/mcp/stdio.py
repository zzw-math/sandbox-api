import asyncio
import json
import sys
from typing import Any

from sandbox_api.db import init_db
from sandbox_api.main import executor, manager
from sandbox_api.mcp.protocol import JsonRpcError, SandboxMcpServer, build_error_response


async def run_stdio_server() -> None:
    init_db()
    server = SandboxMcpServer(manager=manager, executor=executor)

    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if line == "":
                break

            raw_message = line.strip()
            if not raw_message:
                continue

            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                response = build_error_response(None, JsonRpcError(-32700, "Parse error"))
                _write_message(response)
                continue

            request_id = message.get("id")
            try:
                response = await server.handle_message(message)
            except JsonRpcError as exc:
                response = build_error_response(request_id, exc)
            except Exception as exc:
                print(f"[sandbox-api-mcp] internal error: {exc}", file=sys.stderr)
                response = build_error_response(
                    request_id,
                    JsonRpcError(-32603, "Internal error"),
                )

            if response is not None:
                _write_message(response)
    finally:
        await server.shutdown()


def _write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main() -> None:
    asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
