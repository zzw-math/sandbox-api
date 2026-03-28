from pathlib import Path


def create_sandbox(client, tenant_id: str = "tenant-demo") -> str:
    response = client.post(
        "/v1/sandboxes",
        json={"tenantId": tenant_id, "metadata": {"agent": "test-agent"}},
    )
    assert response.status_code == 200, response.text
    return response.json()["sandboxId"]


def test_create_and_get_sandbox(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    sandbox_root = app_env["sandboxes_dir"] / sandbox_id
    assert sandbox_root.exists()
    assert (sandbox_root / "workspace").exists()

    response = client.get(f"/v1/sandboxes/{sandbox_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sandboxId"] == sandbox_id
    assert payload["tenantId"] == "tenant-demo"
    assert payload["status"] == "ready"


def test_write_and_read_file(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    write_response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-write",
            "tool": "write",
            "args": {"path": "notes/hello.txt", "content": "hello sandbox"},
        },
    )
    assert write_response.status_code == 200, write_response.text
    write_payload = write_response.json()["result"]
    assert write_payload["bytesWritten"] == len("hello sandbox".encode("utf-8"))
    assert write_payload["workspaceUsageBytes"] >= write_payload["bytesWritten"]

    file_path = app_env["sandboxes_dir"] / sandbox_id / "workspace" / "notes" / "hello.txt"
    assert file_path.read_text() == "hello sandbox"

    read_response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-read",
            "tool": "read",
            "args": {"path": "notes/hello.txt"},
        },
    )
    assert read_response.status_code == 200
    assert read_response.json()["result"]["content"] == "hello sandbox"


def test_path_escape_is_rejected(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-escape",
            "tool": "write",
            "args": {"path": "../escape.txt", "content": "nope"},
        },
    )
    assert response.status_code == 400
    assert "Path escapes sandbox workspace" in response.json()["detail"]


def test_stop_resume_and_purge_sandbox(app_env):
    client = app_env["client"]
    runtime = app_env["runtime"]
    sandbox_id = create_sandbox(client)

    stop_response = client.delete(f"/v1/sandboxes/{sandbox_id}?purge=false")
    assert stop_response.status_code == 200
    assert sandbox_id in runtime.stopped

    blocked_response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-blocked",
            "tool": "read",
            "args": {"path": "notes/hello.txt"},
        },
    )
    assert blocked_response.status_code == 409

    resume_response = client.post(f"/v1/sandboxes/{sandbox_id}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "ready"

    purge_response = client.delete(f"/v1/sandboxes/{sandbox_id}?purge=true")
    assert purge_response.status_code == 200
    assert sandbox_id in runtime.purged
    assert not (app_env["sandboxes_dir"] / sandbox_id).exists()

    missing_response = client.get(f"/v1/sandboxes/{sandbox_id}")
    assert missing_response.status_code == 404


def test_capacity_limit_returns_429(app_env):
    client = app_env["client"]
    create_sandbox(client, tenant_id="tenant-a")
    create_sandbox(client, tenant_id="tenant-b")

    response = client.post(
        "/v1/sandboxes",
        json={"tenantId": "tenant-c", "metadata": {}},
    )
    assert response.status_code == 429
    assert "max_sandboxes=2" in response.json()["detail"]


def test_workspace_soft_limit_rejects_large_write(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-big",
            "tool": "write",
            "args": {"path": "big.txt", "content": "x" * 128},
        },
    )
    assert response.status_code == 409
    assert "Workspace soft limit exceeded by write" in response.json()["detail"]


def test_bash_returns_metrics(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-bash",
            "tool": "bash",
            "args": {"command": "echo hi", "timeoutMs": 1000},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["exitCode"] == 0
    assert result["stdout"] == "ran:echo hi"
    assert result["timedOut"] is False
    assert "workspaceUsageBytes" in result
    assert "workspaceLimitExceeded" in result


def test_duplicate_request_id_returns_409(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    payload = {
        "sandboxId": sandbox_id,
        "requestId": "req-dup",
        "tool": "bash",
        "args": {"command": "echo hi", "timeoutMs": 1000},
    }
    first = client.post("/v1/tool-call", json=payload)
    assert first.status_code == 200

    second = client.post("/v1/tool-call", json=payload)
    assert second.status_code == 409


def test_bash_timeout_shape_is_exposed(app_env):
    client = app_env["client"]
    sandbox_id = create_sandbox(client)

    response = client.post(
        "/v1/tool-call",
        json={
            "sandboxId": sandbox_id,
            "requestId": "req-timeout",
            "tool": "bash",
            "args": {"command": "timeout-case", "timeoutMs": 10},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["timedOut"] is True
    assert result["sandboxRecreated"] is True
