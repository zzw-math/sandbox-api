import asyncio
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sandbox_api.config import settings
from sandbox_api.db import get_connection
from sandbox_api.errors import CapacityExceededError
from sandbox_api.runtime.base import Runtime


@dataclass
class SandboxRecord:
    sandbox_id: str
    tenant_id: str
    status: str
    root_path: Path
    workspace_path: Path
    metadata: dict
    created_at: datetime
    last_active_at: datetime


class SandboxManager:
    def __init__(self, runtime: Runtime) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self.runtime = runtime

    def get_lock(self, sandbox_id: str) -> asyncio.Lock:
        if sandbox_id not in self._locks:
            self._locks[sandbox_id] = asyncio.Lock()
        return self._locks[sandbox_id]

    async def create_sandbox(
        self,
        tenant_id: str,
        metadata: dict,
    ) -> SandboxRecord:
        if self.count_sandboxes() >= settings.max_sandboxes:
            raise CapacityExceededError(
                f"Sandbox capacity exceeded: max_sandboxes={settings.max_sandboxes}"
            )

        sandbox_id = f"sbx_{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC)
        root_path = settings.sandboxes_dir / sandbox_id
        workspace_path = root_path / "workspace"
        meta_path = root_path / "meta"
        logs_path = root_path / "logs"
        tmp_path = root_path / "tmp"

        workspace_path.mkdir(parents=True, exist_ok=False)
        meta_path.mkdir(parents=True, exist_ok=True)
        logs_path.mkdir(parents=True, exist_ok=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            await self.runtime.ensure_sandbox(sandbox_id, workspace_path)
        except Exception:
            if root_path.exists():
                shutil.rmtree(root_path)
            raise

        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sandboxes (
                    id,
                    tenant_id,
                    status,
                    root_path,
                    workspace_path,
                    metadata_json,
                    created_at,
                    last_active_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sandbox_id,
                    tenant_id,
                    "ready",
                    str(root_path),
                    str(workspace_path),
                    json.dumps(metadata),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

        return SandboxRecord(
            sandbox_id=sandbox_id,
            tenant_id=tenant_id,
            status="ready",
            root_path=root_path,
            workspace_path=workspace_path,
            metadata=metadata,
            created_at=now,
            last_active_at=now,
        )

    def get_sandbox(self, sandbox_id: str) -> SandboxRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM sandboxes WHERE id = ?",
                (sandbox_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def count_sandboxes(self) -> int:
        with get_connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM sandboxes").fetchone()
        return int(row["count"])

    async def resume_sandbox(self, sandbox_id: str) -> SandboxRecord:
        sandbox = self.get_sandbox(sandbox_id)
        if sandbox is None:
            raise KeyError(f"Sandbox {sandbox_id} not found")

        sandbox.root_path.mkdir(parents=True, exist_ok=True)
        sandbox.workspace_path.mkdir(parents=True, exist_ok=True)
        (sandbox.root_path / "meta").mkdir(parents=True, exist_ok=True)
        (sandbox.root_path / "logs").mkdir(parents=True, exist_ok=True)
        (sandbox.root_path / "tmp").mkdir(parents=True, exist_ok=True)
        await self.runtime.ensure_sandbox(sandbox_id, sandbox.workspace_path)

        with get_connection() as connection:
            connection.execute(
                "UPDATE sandboxes SET status = ?, last_active_at = ? WHERE id = ?",
                ("ready", datetime.now(UTC).isoformat(), sandbox_id),
            )
        return self.get_sandbox(sandbox_id)  # type: ignore[return-value]

    async def delete_sandbox(self, sandbox_id: str, purge: bool) -> None:
        sandbox = self.get_sandbox(sandbox_id)
        if sandbox is None:
            raise KeyError(f"Sandbox {sandbox_id} not found")

        if not purge:
            await self.runtime.stop_sandbox(sandbox_id)
            with get_connection() as connection:
                connection.execute(
                    "UPDATE sandboxes SET status = ? WHERE id = ?",
                    ("stopped", sandbox_id),
                )
            return

        await self.runtime.purge_sandbox(sandbox_id)

        with get_connection() as connection:
            connection.execute("DELETE FROM tool_calls WHERE sandbox_id = ?", (sandbox_id,))
            connection.execute("DELETE FROM sandboxes WHERE id = ?", (sandbox_id,))

        if sandbox.root_path.exists():
            shutil.rmtree(sandbox.root_path)

    def touch_sandbox(self, sandbox_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute(
                "UPDATE sandboxes SET last_active_at = ? WHERE id = ?",
                (now, sandbox_id),
            )

    def record_tool_call_start(
        self,
        request_id: str,
        sandbox_id: str,
        tool: str,
        args: dict,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls (
                    request_id,
                    sandbox_id,
                    tool,
                    status,
                    args_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    sandbox_id,
                    tool,
                    "running",
                    json.dumps(args),
                    now,
                ),
            )

    def record_tool_call_finish(
        self,
        request_id: str,
        status: str,
        result: dict | None = None,
        error_text: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE tool_calls
                SET status = ?, result_json = ?, error_text = ?, finished_at = ?
                WHERE request_id = ?
                """,
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error_text,
                    now,
                    request_id,
                ),
            )

    def _row_to_record(self, row) -> SandboxRecord:
        return SandboxRecord(
            sandbox_id=row["id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            root_path=Path(row["root_path"]),
            workspace_path=Path(row["workspace_path"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_active_at=datetime.fromisoformat(row["last_active_at"]),
        )
