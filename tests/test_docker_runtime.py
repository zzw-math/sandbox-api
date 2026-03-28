import asyncio
from pathlib import Path

from sandbox_api.runtime.docker import DockerRuntime


def test_run_bash_recreates_sandbox_on_subprocess_timeout(monkeypatch, tmp_path):
    runtime = DockerRuntime()
    recreated = {"value": False}

    async def fake_ensure_sandbox(sandbox_id: str, workspace: Path) -> str:
        return f"sandbox-{sandbox_id}"

    async def fake_run_docker_with_output(*args, **kwargs):
        raise asyncio.TimeoutError

    async def fake_recreate_sandbox(sandbox_id: str, workspace: Path) -> None:
        recreated["value"] = True

    monkeypatch.setattr(runtime, "ensure_sandbox", fake_ensure_sandbox)
    monkeypatch.setattr(runtime, "_run_docker_with_output", fake_run_docker_with_output)
    monkeypatch.setattr(runtime, "_recreate_sandbox", fake_recreate_sandbox)

    result = asyncio.run(
        runtime.run_bash(
            sandbox_id="sbx_timeout",
            workspace=tmp_path,
            command="sleep 60",
            timeout_ms=10,
        )
    )

    assert recreated["value"] is True
    assert result["timedOut"] is True
    assert result["sandboxRecreated"] is True
    assert result["exitCode"] == -1


def test_run_bash_recreates_sandbox_on_timeout_exit_code(monkeypatch, tmp_path):
    runtime = DockerRuntime()
    recreated = {"value": False}

    async def fake_ensure_sandbox(sandbox_id: str, workspace: Path) -> str:
        return f"sandbox-{sandbox_id}"

    async def fake_run_docker_with_output(*args, **kwargs):
        return "partial", "timed out", 124

    async def fake_recreate_sandbox(sandbox_id: str, workspace: Path) -> None:
        recreated["value"] = True

    monkeypatch.setattr(runtime, "ensure_sandbox", fake_ensure_sandbox)
    monkeypatch.setattr(runtime, "_run_docker_with_output", fake_run_docker_with_output)
    monkeypatch.setattr(runtime, "_recreate_sandbox", fake_recreate_sandbox)

    result = asyncio.run(
        runtime.run_bash(
            sandbox_id="sbx_timeout_exit",
            workspace=tmp_path,
            command="sleep 60",
            timeout_ms=10,
        )
    )

    assert recreated["value"] is True
    assert result["stdout"] == "partial"
    assert result["timedOut"] is True
    assert result["sandboxRecreated"] is True
