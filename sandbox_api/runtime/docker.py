import asyncio
from pathlib import Path
from typing import Any

from sandbox_api.config import settings
from sandbox_api.runtime.base import Runtime


class DockerRuntime(Runtime):
    def _container_name(self, sandbox_id: str) -> str:
        return f"sandbox-{sandbox_id}"

    async def ensure_sandbox(self, sandbox_id: str, workspace: Path) -> str:
        name = self._container_name(sandbox_id)
        status = await self._container_status(name)

        if status == "running":
            return name
        if status in {"created", "exited"}:
            await self._run_docker("start", name)
            return name
        if status == "paused":
            await self._run_docker("unpause", name)
            return name
        if status in {"restarting", "removing"}:
            raise RuntimeError(f"Container {name} is busy with Docker state: {status}")
        if status == "dead":
            await self._run_docker("rm", "-f", name)
            status = None
        if status is not None:
            return name

        workspace.mkdir(parents=True, exist_ok=True)
        await self._run_docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"sandbox.id={sandbox_id}",
            "--network",
            settings.docker_network,
            "--memory",
            settings.docker_memory,
            "--cpus",
            settings.docker_cpus,
            "--pids-limit",
            settings.docker_pids_limit,
            "-w",
            "/workspace",
            "-v",
            f"{workspace}:/workspace",
            settings.docker_image,
            "sleep",
            "infinity",
        )
        return name

    async def stop_sandbox(self, sandbox_id: str) -> None:
        name = self._container_name(sandbox_id)
        status = await self._container_status(name)
        if status is None:
            return
        if status != "running":
            return
        await self._run_docker("stop", name)

    async def purge_sandbox(self, sandbox_id: str) -> None:
        name = self._container_name(sandbox_id)
        status = await self._container_status(name)
        if status is None:
            return
        await self._run_docker("rm", "-f", name)

    async def run_bash(
        self,
        sandbox_id: str,
        workspace: Path,
        command: str,
        timeout_ms: int,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        name = await self.ensure_sandbox(sandbox_id, workspace)

        docker_args = ["exec", "-w", "/workspace"]
        if env:
            for key, value in env.items():
                docker_args.extend(["-e", f"{key}={value}"])
        docker_args.extend([name, settings.docker_shell, "-lc", command])

        try:
            stdout, stderr, returncode = await self._run_docker_with_output(
                *docker_args,
                timeout_ms=timeout_ms,
            )
        except asyncio.TimeoutError:
            return {
                "exitCode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout_ms}ms",
            }

        return {
            "exitCode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    async def _container_status(self, name: str) -> str | None:
        stdout, stderr, returncode = await self._run_docker_with_output(
            "inspect",
            "-f",
            "{{.State.Status}}",
            name,
            timeout_ms=10_000,
            allow_failure=True,
        )
        if returncode != 0:
            normalized_error = stderr.lower()
            if "no such object" in normalized_error or "no such container" in normalized_error:
                return None
            raise RuntimeError(stderr or f"docker inspect failed for container {name}")
        return stdout.strip()

    async def _run_docker(self, *args: str) -> None:
        _, stderr, returncode = await self._run_docker_with_output(*args, timeout_ms=30_000)
        if returncode != 0:
            raise RuntimeError(stderr or f"docker {' '.join(args)} failed")

    async def _run_docker_with_output(
        self,
        *args: str,
        timeout_ms: int,
        allow_failure: bool = False,
    ) -> tuple[str, str, int]:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise

        stdout = stdout_raw.decode("utf-8", errors="replace")
        stderr = stderr_raw.decode("utf-8", errors="replace")
        if process.returncode != 0 and not allow_failure:
            raise RuntimeError(stderr or f"docker {' '.join(args)} failed")
        return stdout, stderr, process.returncode
