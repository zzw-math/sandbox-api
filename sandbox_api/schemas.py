from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateSandboxRequest(BaseModel):
    tenant_id: str = Field(default="default", alias="tenantId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxResponse(BaseModel):
    sandbox_id: str = Field(alias="sandboxId")
    tenant_id: str = Field(alias="tenantId")
    status: str
    workspace_path: str = Field(alias="workspacePath")
    created_at: datetime = Field(alias="createdAt")
    last_active_at: datetime = Field(alias="lastActiveAt")

    model_config = {"populate_by_name": True}


class ToolCallRequest(BaseModel):
    sandbox_id: str = Field(alias="sandboxId")
    request_id: str = Field(alias="requestId")
    tool: Literal["read", "write", "bash"]
    args: dict[str, Any]

    model_config = {"populate_by_name": True}


class ToolCallResponse(BaseModel):
    request_id: str = Field(alias="requestId")
    sandbox_id: str = Field(alias="sandboxId")
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None

    model_config = {"populate_by_name": True}

