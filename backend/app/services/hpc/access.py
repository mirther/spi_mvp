from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.hpc_access import HPCAccessLease

_FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]+={0,2}")


class HPCAccessError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _windows_only() -> None:
    if os.name != "nt":
        raise HPCAccessError(
            "Native Windows SSH Agent management is available only when SPI backend runs on Windows.",
            501,
        )


def _find_executable(name: str) -> str:
    located = shutil.which(name)
    if located:
        return located
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidate = Path(windir) / "System32" / "OpenSSH" / f"{name}.exe"
    if candidate.is_file():
        return str(candidate)
    raise HPCAccessError(f"Windows OpenSSH executable '{name}.exe' was not found.", 503)


def _run_local(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise HPCAccessError(f"{Path(args[0]).name} timed out.", 503) from exc
    except OSError as exc:
        raise HPCAccessError(f"Could not run {Path(args[0]).name}: {exc}", 503) from exc


def _public_key_fingerprint() -> str:
    _windows_only()
    key_path = settings.hpc_ssh_public_key_path.expanduser()
    if not key_path.is_file():
        raise HPCAccessError(
            f"SPI's configured public key was not found: {key_path}. "
            "Set SPI_HPC_SSH_PUBLIC_KEY_PATH in the project .env file.",
            503,
        )
    ssh_keygen = _find_executable("ssh-keygen")
    result = _run_local([ssh_keygen, "-lf", str(key_path)])
    if result.returncode != 0:
        raise HPCAccessError(
            result.stderr.strip() or "Could not read the configured SSH public key.", 503
        )
    match = _FINGERPRINT_RE.search(result.stdout)
    if not match:
        raise HPCAccessError("Could not determine the configured SSH public-key fingerprint.", 503)
    return match.group(0)


def _agent_fingerprints() -> set[str]:
    _windows_only()
    ssh_add = _find_executable("ssh-add")
    result = _run_local([ssh_add, "-l"])
    output = f"{result.stdout}\n{result.stderr}"

    if result.returncode != 0:
        return set()

    return set(_FINGERPRINT_RE.findall(output))


def _identity_is_loaded(fingerprint: str) -> bool:
    return fingerprint in _agent_fingerprints()


def get_hpc_key_status() -> dict:
    fingerprint = _public_key_fingerprint()
    return {
        "loaded": _identity_is_loaded(fingerprint)
    }


def _powershell(script: str, timeout: int = 20) -> str:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
    result = _run_local(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        timeout=timeout,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Windows Task Scheduler operation failed."
        raise HPCAccessError(message[-2000:], 503)
    return result.stdout.strip()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _schedule_identity_removal(expires_at: datetime) -> None:
    _windows_only()
    ssh_add = _find_executable("ssh-add")
    public_key = str(settings.hpc_ssh_public_key_path.expanduser().resolve())
    task_name = settings.hpc_access_task_name
    # Use an offset-aware timestamp and let Task Scheduler execute as this
    # interactive user, so ssh-add talks to this user's Windows OpenSSH Agent.
    expiry_iso = expires_at.astimezone().isoformat(timespec="seconds")
    script = f"""
$ErrorActionPreference = 'Stop'
$taskName = {_ps_quote(task_name)}
$sshAdd = {_ps_quote(ssh_add)}
$publicKey = {_ps_quote(public_key)}
$expiry = [DateTimeOffset]::Parse({_ps_quote(expiry_iso)}).LocalDateTime
$action = New-ScheduledTaskAction -Execute $sshAdd -Argument ('-d ' + '"' + $publicKey + '"')
$trigger = New-ScheduledTaskTrigger -Once -At $expiry
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Removes the SPI HPC SSH identity from the current user OpenSSH Agent at the end of its access lease.' -Force | Out-Null
if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {{ throw 'SPI SSH identity expiry task was not registered.' }}
Write-Output 'SPI_HPC_EXPIRY_TASK_REGISTERED'
"""
    output = _powershell(script)
    if "SPI_HPC_EXPIRY_TASK_REGISTERED" not in output:
        raise HPCAccessError("Windows did not confirm registration of the SSH identity expiry task.", 503)


def _remove_expiry_task() -> None:
    if os.name != "nt":
        return
    script = f"""
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName {_ps_quote(settings.hpc_access_task_name)} -Confirm:$false -ErrorAction SilentlyContinue
Write-Output 'SPI_HPC_EXPIRY_TASK_REMOVED'
"""
    try:
        _powershell(script)
    except HPCAccessError:
        # The expiry task may already have executed or been removed manually.
        pass


def _remove_identity() -> None:
    if os.name != "nt":
        return
    try:
        ssh_add = _find_executable("ssh-add")
        key_path = str(settings.hpc_ssh_public_key_path.expanduser().resolve())
        _run_local([ssh_add, "-d", key_path])
    except HPCAccessError:
        # Expiry remains effective in the database even if the agent removal
        # command cannot run; callers are denied remote SSH by lease checks.
        pass


def _open_private_key_loader() -> None:
    """Open a native console for ssh-add; the passphrase never enters FastAPI."""
    _windows_only()
    private_key = settings.hpc_ssh_private_key_path
    if private_key is None:
        raise HPCAccessError(
            "The SPI HPC key is not currently loaded in Windows OpenSSH Agent. "
            "To enable the Connect button to prompt for its passphrase, set "
            "SPI_HPC_SSH_PRIVATE_KEY_PATH to the matching OpenSSH-format private key in .env. "
            "A PuTTY .ppk file cannot be passed directly to ssh-add. You can also load the key "
            "into Windows OpenSSH Agent yourself and click Connect again."
        )
    private_key = private_key.expanduser().resolve()
    if private_key.suffix.lower() == ".ppk":
        raise HPCAccessError(
            "SPI_HPC_SSH_PRIVATE_KEY_PATH points to a PuTTY .ppk file. Export an OpenSSH-format "
            "private key with PuTTYgen and configure that exported file instead."
        )
    if not private_key.is_file():
        raise HPCAccessError(f"Configured OpenSSH private key was not found: {private_key}", 503)

    ssh_add = _find_executable("ssh-add")
    command = (
        f"& {_ps_quote(ssh_add)} {_ps_quote(str(private_key))}; "
        "if ($LASTEXITCODE -eq 0) { "
        "Write-Host 'SPI HPC identity loaded successfully.' -ForegroundColor Green; "
        "Start-Sleep -Seconds 1; exit 0 "
        "} else { "
        "Write-Host 'The identity was not loaded. Check the passphrase and key format.' -ForegroundColor Red; "
        "Read-Host 'Press Enter to close this window'; exit 1 "
        "}"
    )
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
    try:
        subprocess.Popen(
            [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            close_fds=True,
        )
    except OSError as exc:
        raise HPCAccessError(f"Could not open the SSH key-loading window: {exc}", 503) from exc


def _expire_record(db: Session, record: HPCAccessLease) -> None:
    _remove_identity()
    _remove_expiry_task()
    record.state = "expired"
    db.commit()


def get_hpc_access_status(db: Session) -> dict:
    record = db.get(HPCAccessLease, 1)
    if record is None:
        return {
            "state": "disconnected",
            "connected": False,
            "connectedAt": None,
            "expiresAt": None,
            "remainingSeconds": 0,
            "leaseMinutes": settings.hpc_access_lease_minutes,
        }

    now = _utc_now()
    expires_at = _normalise_utc(record.expires_at)
    if record.state == "connected" and expires_at <= now:
        _expire_record(db, record)
    elif record.state == "connected" and not _identity_is_loaded(record.fingerprint):
        _remove_expiry_task()
        record.state = "disconnected"
        db.commit()

    is_connected = record.state == "connected" and expires_at > now
    remaining = max(0, int((expires_at - now).total_seconds())) if is_connected else 0
    return {
        "state": record.state,
        "connected": is_connected,
        "connectedAt": record.connected_at.isoformat() if record.connected_at else None,
        "expiresAt": expires_at.isoformat(),
        "remainingSeconds": remaining,
        "leaseMinutes": settings.hpc_access_lease_minutes,
    }


def connect_hpc_access(db: Session) -> dict:
    _windows_only()
    fingerprint = _public_key_fingerprint()

    if not _identity_is_loaded(fingerprint):
        _open_private_key_loader()
        return {
            "state": "awaiting_key",
            "connected": False,
            "message": "Waiting for SSH identity to be loaded."
        }

    # Verify a real SSH login before creating a lease. This probe is the only
    # path allowed to bypass the lease guard.
    from app.services.hpc.ssh import HPCConnectionError, run_ssh

    try:
        probe = run_ssh("echo SPI_HPC_ACCESS_OK && hostname", require_hpc_access=False)
    except HPCConnectionError as exc:
        raise HPCAccessError(f"SSH identity is loaded, but the HPC connection failed: {exc}", 503) from exc
    if "SPI_HPC_ACCESS_OK" not in probe.splitlines():
        raise HPCAccessError("HPC did not return the expected connection confirmation.", 503)

    now = _utc_now()
    expires_at = now + timedelta(minutes=settings.hpc_access_lease_minutes)
    _schedule_identity_removal(expires_at)

    record = db.get(HPCAccessLease, 1)
    if record is None:
        record = HPCAccessLease(
            id=1,
            fingerprint=fingerprint,
            public_key_path=str(settings.hpc_ssh_public_key_path.expanduser().resolve()),
            connected_at=now,
            expires_at=expires_at,
            state="connected",
        )
        db.add(record)
    else:
        record.fingerprint = fingerprint
        record.public_key_path = str(settings.hpc_ssh_public_key_path.expanduser().resolve())
        record.connected_at = now
        record.expires_at = expires_at
        record.state = "connected"
    db.commit()
    return get_hpc_access_status(db)


def disconnect_hpc_access(db: Session) -> dict:
    _windows_only()
    record = db.get(HPCAccessLease, 1)
    _remove_identity()
    _remove_expiry_task()
    if record is not None:
        record.state = "disconnected"
        db.commit()
    return get_hpc_access_status(db)


def assert_hpc_access_active() -> None:
    """Fail closed before any remote SSH operation if the 60-minute lease is absent."""
    _windows_only()
    with SessionLocal() as db:
        record = db.get(HPCAccessLease, 1)
        if record is None or record.state != "connected":
            raise HPCAccessError("HPC access is not connected. Use Connect to HPC in SPI first.", 503)
        if _normalise_utc(record.expires_at) <= _utc_now():
            _expire_record(db, record)
            raise HPCAccessError("HPC access lease expired. Reconnect to HPC to continue.", 503)
        if not _identity_is_loaded(record.fingerprint):
            _remove_expiry_task()
            record.state = "disconnected"
            db.commit()
            raise HPCAccessError("The SPI SSH identity is no longer loaded. Reconnect to HPC.", 503)
