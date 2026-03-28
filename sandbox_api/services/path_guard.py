from pathlib import Path


def resolve_safe_path(workspace: Path, user_path: str) -> Path:
    if not user_path or Path(user_path).is_absolute():
        raise ValueError("Path must be a non-empty relative path")

    resolved = (workspace / user_path).resolve()
    workspace_root = workspace.resolve()

    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("Path escapes sandbox workspace") from exc

    return resolved

