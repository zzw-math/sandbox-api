import sqlite3

from sandbox_api.config import settings


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.sandboxes_dir.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sandboxes (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                root_path TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
                request_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL,
                tool TEXT NOT NULL,
                status TEXT NOT NULL,
                args_json TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (sandbox_id) REFERENCES sandboxes(id)
            )
            """
        )

