from pathlib import Path


def workspace_size_bytes(workspace: Path) -> int:
    total = 0
    if not workspace.exists():
        return total

    for path in workspace.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def projected_workspace_size_bytes(
    workspace: Path,
    target: Path,
    new_content_size: int,
    mode: str,
) -> int:
    current_workspace_size = workspace_size_bytes(workspace)
    existing_file_size = target.stat().st_size if target.exists() and target.is_file() else 0

    if mode == "append":
        return current_workspace_size + new_content_size
    return current_workspace_size - existing_file_size + new_content_size
