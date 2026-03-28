class SandboxError(RuntimeError):
    """Base error type for sandbox API domain failures."""


class CapacityExceededError(SandboxError):
    """Raised when the service reached its configured capacity."""


class WorkspaceLimitExceededError(SandboxError):
    """Raised when a workspace exceeds the configured quota."""
