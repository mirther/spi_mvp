from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pathlib import PurePosixPath
from shlex import quote

from app.core.config import settings
from app.db.session import get_db
from app.services.hpc.access import (
    HPCAccessError,
    connect_hpc_access,
    disconnect_hpc_access,
    get_hpc_access_status,
    get_hpc_key_status,
)
from app.services.hpc.ssh import (
    HPCConnectionError,
    run_ssh,
)


router = APIRouter()


@router.get("/access")
def hpc_access_status(db: Session = Depends(get_db)):
    try:
        return get_hpc_access_status(db)
    except HPCAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/access/key-status")
def hpc_access_key_status():
    try:
        return get_hpc_key_status()
    except HPCAccessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc)
        ) from exc


@router.post("/access/connect")
def hpc_access_connect(db: Session = Depends(get_db)):
    try:
        return connect_hpc_access(db)
    except HPCAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/access/disconnect")
def hpc_access_disconnect(db: Session = Depends(get_db)):
    try:
        return disconnect_hpc_access(db)
    except HPCAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/status")
def hpc_status():
    try:
        output = run_ssh(
            "echo SPI_HPC_OK && hostname"
        )

        parts = output.split("\n", 1)

        if not parts or parts[0] != "SPI_HPC_OK":
            raise HPCConnectionError(
                "Unexpected response from HPC."
            )

        remote_hostname = parts[1].strip() if len(parts) > 1 else None

        return {
            "connected": True,
            "hostname": remote_hostname,
            "remote_root": settings.hpc_root,
        }

    except HPCConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )


@router.post("/initialize")
def initialize_hpc():
    root = PurePosixPath(settings.hpc_root)

    directories = [
        root / "storage" / "data",
        root / "projects",
        root / "workers",
        root / "tmp",
    ]

    paths = " ".join(quote(str(path)) for path in directories)

    writable_paths = " && ".join(
        f"test -w {quote(str(path))}" for path in directories
    )

    command = (
        f"mkdir -p {paths} && "
        f"{writable_paths} && "
        "echo SPI_HPC_READY"
    )

    try:
        output = run_ssh(command)

        if "SPI_HPC_READY" not in output.splitlines():
            raise HPCConnectionError(
                "HPC directory initialization was not confirmed."
            )

        return {
            "initialized": True,
            "directories": [str(path) for path in directories],
        }

    except HPCConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )

@router.get("/worker")
def hpc_worker_check():
    """Inspect the expected remote worker executable and its --request CLI option."""
    python_path = quote(str(settings.hpc_python))
    runner_path = quote(str(settings.hpc_worker_runner))
    command = (
        f"if [ -x {python_path} ] && [ -f {runner_path} ]; then "
        f"echo SPI_WORKER_FILES_OK; "
        f"grep -n -- '--request' {runner_path} || true; "
        "else echo SPI_WORKER_FILES_MISSING; fi"
    )
    try:
        output = run_ssh(command)
    except HPCConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    files_ready = "SPI_WORKER_FILES_OK" in output.splitlines()
    request_supported = "--request" in output
    return {
        "files_ready": files_ready,
        "request_argument_supported": request_supported,
        "python_path": str(settings.hpc_python),
        "runner_path": str(settings.hpc_worker_runner),
        "help_output": output.replace("SPI_WORKER_FILES_OK", "").strip()[:4000],
    }
