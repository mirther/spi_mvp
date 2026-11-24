from pathlib import Path, PurePosixPath

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    database_path: Path = PROJECT_ROOT / "database" / "spi.db"

    hpc_host: str | None = None
    hpc_user: str | None = None
    hpc_port: int = 22
    hpc_root: str = "/home/username/spi"
    hpc_ssh_public_key_path: Path = Field(
        default_factory=lambda: Path.home() / ".ssh" / "spi_hpc.pub"
    )
    # Optional OpenSSH-format private key. Never point this at a .ppk file.
    # If configured, SPI opens an interactive terminal to run ssh-add and prompt locally.
    hpc_ssh_private_key_path: Path | None = None
    hpc_access_lease_minutes: int = Field(default=60, ge=1, le=240)
    hpc_access_task_name: str = "SPI-Expire-HPC-SSH-Identity"

    session_cookie_name: str = "spi_session"
    csrf_cookie_name: str = "spi_csrf"
    csrf_header_name: str = "X-SPI-CSRF"
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8
    cookie_secure: bool = False  # Localhost HTTP only; set true when served over HTTPS.
    browser_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174,"
        "http://localhost:8000,http://127.0.0.1:8000"
    )

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="SPI_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_browser_origins(self) -> list[str]:
        return [item.strip() for item in self.browser_origins.split(",") if item.strip()]

    @property
    def hpc_data_dir(self) -> PurePosixPath:
        return PurePosixPath(self.hpc_root) / "storage" / "data"

    @property
    def hpc_projects_dir(self) -> PurePosixPath:
        return PurePosixPath(self.hpc_root) / "projects"

    @property
    def hpc_tmp_dir(self) -> PurePosixPath:
        return PurePosixPath(self.hpc_root) / "tmp"

    @property
    def hpc_python(self) -> PurePosixPath:
        return PurePosixPath(self.hpc_root) / ".venv" / "bin" / "python"

    @property
    def hpc_worker_runner(self) -> PurePosixPath:
        return PurePosixPath(self.hpc_root) / "workers" / "runner.py"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "sqlite",
            database=str(self.database_path),
        )


settings = Settings()