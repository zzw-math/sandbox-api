import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sandbox_api import main as app_main
from sandbox_api.config import settings
from sandbox_api.db import init_db
from sandbox_api.services.sandbox_manager import SandboxManager
from sandbox_api.services.tool_executor import ToolExecutor


class FakeRuntime:
    def __init__(self) -> None:
        self.containers: dict[str, str] = {}
        self.stopped: list[str] = []
        self.purged: list[str] = []
        self.commands: list[tuple[str, str]] = []

    async def ensure_sandbox(self, sandbox_id: str, workspace: Path) -> str:
        workspace.mkdir(parents=True, exist_ok=True)
        self.containers[sandbox_id] = "running"
        return f"sandbox-{sandbox_id}"

    async def stop_sandbox(self, sandbox_id: str) -> None:
        self.stopped.append(sandbox_id)
        self.containers[sandbox_id] = "stopped"

    async def purge_sandbox(self, sandbox_id: str) -> None:
        self.purged.append(sandbox_id)
        self.containers.pop(sandbox_id, None)

    async def run_bash(
        self,
        sandbox_id: str,
        workspace: Path,
        command: str,
        timeout_ms: int,
        env: dict[str, str] | None = None,
    ) -> dict:
        self.commands.append((sandbox_id, command))
        if command == "timeout-case":
            return {
                "exitCode": -1,
                "stdout": "",
                "stderr": "Command timed out after 10ms. Sandbox container was recreated to clean up lingering processes.",
                "timedOut": True,
                "sandboxRecreated": True,
            }
        return {
            "exitCode": 0,
            "stdout": f"ran:{command}",
            "stderr": "",
            "timedOut": False,
            "sandboxRecreated": False,
        }


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    sandboxes_dir = data_dir / "sandboxes"
    db_path = data_dir / "sandbox.db"

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "sandboxes_dir", sandboxes_dir)
    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "workspace_soft_limit_bytes", 64)
    monkeypatch.setattr(settings, "max_sandboxes", 2)
    monkeypatch.setattr(settings, "max_concurrent_bash", 4)
    monkeypatch.setattr(settings, "max_concurrent_lifecycle", 2)

    if data_dir.exists():
        shutil.rmtree(data_dir)
    init_db()

    runtime = FakeRuntime()
    manager = SandboxManager(runtime=runtime)
    executor = ToolExecutor(runtime=runtime)

    monkeypatch.setattr(app_main, "runtime", runtime)
    monkeypatch.setattr(app_main, "manager", manager)
    monkeypatch.setattr(app_main, "executor", executor)

    with TestClient(app_main.app) as client:
        yield {
            "client": client,
            "runtime": runtime,
            "manager": manager,
            "data_dir": data_dir,
            "sandboxes_dir": sandboxes_dir,
            "db_path": db_path,
        }
