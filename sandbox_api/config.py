import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self.data_dir = self.project_root / "data"
        self.sandboxes_dir = self.data_dir / "sandboxes"
        self.db_path = self.data_dir / "sandbox.db"
        self.default_tenant_id = "default"
        self.docker_image = os.getenv("SANDBOX_DOCKER_IMAGE", "ubuntu:24.04")
        self.docker_shell = os.getenv("SANDBOX_DOCKER_SHELL", "/bin/bash")
        self.docker_network = os.getenv("SANDBOX_DOCKER_NETWORK", "none")
        self.docker_memory = os.getenv("SANDBOX_DOCKER_MEMORY", "512m")
        self.docker_cpus = os.getenv("SANDBOX_DOCKER_CPUS", "1.0")
        self.docker_pids_limit = os.getenv("SANDBOX_DOCKER_PIDS_LIMIT", "256")


settings = Settings()
