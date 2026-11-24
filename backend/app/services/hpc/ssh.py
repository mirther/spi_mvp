from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings


class HPCConnectionError(Exception):
    pass


def _find_ssh_executable() -> str:
    """Prefer Windows OpenSSH, matching the ssh-add executable used by SPI."""
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        windows_ssh = Path(windir) / "System32" / "OpenSSH" / "ssh.exe"
        if windows_ssh.is_file():
            return str(windows_ssh)

    executable = shutil.which("ssh")
    if executable:
        return executable

    raise HPCConnectionError("OpenSSH executable was not found.")


def run_ssh(command: str, *, require_hpc_access: bool = True) -> str:
    if require_hpc_access:
        from app.services.hpc.access import HPCAccessError, assert_hpc_access_active

        try:
            assert_hpc_access_active()
        except HPCAccessError as exc:
            raise HPCConnectionError(str(exc)) from exc

    if not settings.hpc_host or not settings.hpc_user:
        raise HPCConnectionError("HPC host or username is not configured.")

    ssh_executable = _find_ssh_executable()
    destination = f"{settings.hpc_user}@{settings.hpc_host}"

    ssh_command = [
        ssh_executable,
        "-T",
        "-n",
        "-p", str(settings.hpc_port),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        destination,
        command,
    ]

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    try:
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise HPCConnectionError("SSH command timed out.") from exc
    except FileNotFoundError as exc:
        raise HPCConnectionError(
            f"OpenSSH executable was not found: {ssh_executable}"
        ) from exc

    if result.returncode != 0:
        raise HPCConnectionError(result.stderr.strip() or "SSH command failed.")

    return result.stdout.strip()
