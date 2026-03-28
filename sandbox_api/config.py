import os
import tomllib
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self.data_dir = self.project_root / "data"
        self.sandboxes_dir = self.data_dir / "sandboxes"
        self.db_path = self.data_dir / "sandbox.db"
        self.config_path = Path(
            os.getenv(
                "SANDBOX_CONFIG_PATH",
                self.project_root / "config" / "sandbox.toml",
            )
        )
        self.default_tenant_id = "default"

        config_data = self._load_config_file()
        docker_config = config_data.get("docker", {})
        scheduler_config = config_data.get("scheduler", {})

        self.docker_image = os.getenv(
            "SANDBOX_DOCKER_IMAGE",
            str(docker_config.get("image", "ubuntu:24.04")),
        )
        self.docker_shell = os.getenv(
            "SANDBOX_DOCKER_SHELL",
            str(docker_config.get("shell", "/bin/bash")),
        )
        self.docker_network = os.getenv(
            "SANDBOX_DOCKER_NETWORK",
            str(docker_config.get("network", "none")),
        )
        self.docker_memory = os.getenv(
            "SANDBOX_DOCKER_MEMORY",
            str(docker_config.get("memory", "512m")),
        )
        self.docker_cpus = os.getenv(
            "SANDBOX_DOCKER_CPUS",
            str(docker_config.get("cpus", "1.0")),
        )
        self.docker_pids_limit = int(
            os.getenv(
                "SANDBOX_DOCKER_PIDS_LIMIT",
                str(docker_config.get("pids_limit", 256)),
            )
        )
        self.workspace_soft_limit_bytes = int(
            os.getenv(
                "SANDBOX_WORKSPACE_SOFT_LIMIT_BYTES",
                str(docker_config.get("workspace_soft_limit_bytes", 268435456)),
            )
        )
        self.docker_stop_timeout_seconds = int(
            os.getenv(
                "SANDBOX_DOCKER_STOP_TIMEOUT_SECONDS",
                str(docker_config.get("stop_timeout_seconds", 1)),
            )
        )
        self.max_sandboxes = int(
            os.getenv(
                "SANDBOX_MAX_SANDBOXES",
                str(scheduler_config.get("max_sandboxes", 20)),
            )
        )
        self.max_concurrent_bash = int(
            os.getenv(
                "SANDBOX_MAX_CONCURRENT_BASH",
                str(scheduler_config.get("max_concurrent_bash", 4)),
            )
        )
        self.max_concurrent_lifecycle = int(
            os.getenv(
                "SANDBOX_MAX_CONCURRENT_LIFECYCLE",
                str(scheduler_config.get("max_concurrent_lifecycle", 2)),
            )
        )

    def _load_config_file(self) -> dict:
        if not self.config_path.exists():
            return {}
        with self.config_path.open("rb") as config_file:
            return tomllib.load(config_file)


settings = Settings()
