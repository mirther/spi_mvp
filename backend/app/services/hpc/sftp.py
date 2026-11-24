import shutil
import subprocess

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.core.config import settings


class HPCTransferError(Exception):
    pass


@dataclass(frozen=True)
class TransferResult:
    local_path: Path
    remote_path: str
    file_size_bytes: int


def _quote_sftp_path(path: str) -> str:
    """Quote a path for the SFTP batch command parser."""

    if "\n" in path or "\r" in path:
        raise ValueError("Paths cannot contain newline characters.")

    escaped = path.replace("\\", "\\\\").replace('"', '\\"')

    return f'"{escaped}"'


def upload_file(
    local_path: str | Path,
    remote_path: str,
    timeout: int = 600,
) -> TransferResult:
    """
    Upload a local file to HPC using OpenSSH SFTP.

    The file is uploaded under a temporary remote name
    and renamed to its final name after successful transfer.
    """

    # Validate local file
    local_path = Path(local_path).resolve(strict=True)

    if not local_path.is_file():
        raise HPCTransferError("Local path is not a file.")

    file_size = local_path.stat().st_size

    # Validate HPC configuration
    if not settings.hpc_host or not settings.hpc_user:
        raise HPCTransferError(
            "HPC host or username is not configured."
        )

    sftp_executable = shutil.which("sftp")

    if not sftp_executable:
        raise HPCTransferError(
            "Windows OpenSSH SFTP executable was not found."
        )

    # Validate remote destination
    destination_path = PurePosixPath(remote_path)

    if not destination_path.is_absolute():
        raise ValueError("Remote path must be absolute.")

    if ".." in destination_path.parts:
        raise ValueError("Remote path cannot contain '..'.")

    temporary_path = PurePosixPath(
        str(destination_path) + f".uploading-{uuid4().hex}"
    )

    # Windows SFTP accepts forward-slash paths.
    local_sftp_path = local_path.as_posix()

    # Construct SFTP batch commands
    batch_commands = (
        f"put {_quote_sftp_path(local_sftp_path)} "
        f"{_quote_sftp_path(str(temporary_path))}\n"
        f"rename {_quote_sftp_path(str(temporary_path))} "
        f"{_quote_sftp_path(str(destination_path))}\n"
    )

    destination = (
        f"{settings.hpc_user}@{settings.hpc_host}"
    )

    command = [
        sftp_executable,
        "-b", "-",
        "-P", str(settings.hpc_port),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        destination,
    ]

    try:
        result = subprocess.run(
            command,
            input=batch_commands,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    except subprocess.TimeoutExpired as exc:
        raise HPCTransferError(
            f"SFTP transfer timed out after {timeout} seconds."
        ) from exc

    except OSError as exc:
        raise HPCTransferError(
            f"Could not start SFTP: {exc}"
        ) from exc

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()

        raise HPCTransferError(
            f"SFTP upload failed: {error}"
        )

    return TransferResult(
        local_path=local_path,
        remote_path=str(destination_path),
        file_size_bytes=file_size,
    )