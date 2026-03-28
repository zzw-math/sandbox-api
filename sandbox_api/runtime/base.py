from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Runtime(ABC):
    @abstractmethod
    async def ensure_sandbox(self, sandbox_id: str, workspace: Path) -> str:
        raise NotImplementedError

    @abstractmethod
    async def stop_sandbox(self, sandbox_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def purge_sandbox(self, sandbox_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def run_bash(
        self,
        sandbox_id: str,
        workspace: Path,
        command: str,
        timeout_ms: int,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
