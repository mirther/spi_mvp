"""Small SLURM submission and monitoring helpers for SPI runs."""

import base64
import re
from pathlib import PurePosixPath
from shlex import quote
from typing import Any

from app.core.config import settings
from app.services.hpc.ssh import HPCConnectionError, run_ssh
from app.services.hpc.storage import (
    HPCStorageError,
    RunStoragePaths,
    build_sbatch_script,
    ensure_remote_run_directory,
)

_JOB_ID = re.compile(r"^[0-9]+$")


class HPCExecutionError(Exception):
    """An SSH, file-transfer, or SLURM submission/monitoring failure."""


def write_remote_text(remote_path: PurePosixPath, content: str) -> None:
    """Write a small UTF-8 file remotely without shell-interpolating its content."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    path = quote(str(remote_path))
    command = (
        f"umask 077; printf %s {quote(encoded)} | base64 -d > {path} "
        f"&& chmod 600 {path}"
    )
    try:
        run_ssh(command)
    except HPCConnectionError as exc:
        raise HPCExecutionError(str(exc)) from exc


def build_run_request(
    *,
    project_id: str,
    run_id: str,
    project_name: str,
    execution_order: list[str],
    nodes: list[Any],
    connections: list[Any],
) -> dict:
    """Build the stable request document consumed by workers/runner.py."""
    nodes_by_id = {node.id: node for node in nodes}
    ordered_modules = []
    for node_id in execution_order:
        node = nodes_by_id[node_id]
        ordered_modules.append(
            {
                "node_id": node.id,
                "module_id": node.module_id,
                "parameters": node.parameters or {},
            }
        )

    return {
        "schema_version": 1,
        "project_id": project_id,
        "project_name": project_name,
        "run_id": run_id,
        "execution_mode": "all",
        "execution_order": execution_order,
        "modules": ordered_modules,
        "connections": [
            {
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
            }
            for edge in connections
        ],
        "storage": {
            "data_directory": str(settings.hpc_data_dir),
            "run_directory": str(settings.hpc_projects_dir / project_id / run_id),
        },
    }


def submit_run(paths: RunStoragePaths, cluster_config: Any, request_document: dict) -> str:
    """Create the per-run files and submit exactly one SLURM job."""
    if not settings.hpc_host or not settings.hpc_user:
        raise HPCExecutionError("HPC host or username is not configured in .env.")

    request_json = __import__("json").dumps(request_document, ensure_ascii=False, indent=2)
    worker_argv = [
        str(settings.hpc_python),
        str(settings.hpc_worker_runner),
        "--request",
        str(paths.request_file),
    ]

    try:
        script = build_sbatch_script(cluster_config, paths, worker_argv)
        ensure_remote_run_directory(paths)
        write_remote_text(paths.request_file, request_json)
        write_remote_text(paths.batch_file, script)
        output = run_ssh(f"sbatch --parsable {quote(str(paths.batch_file))}")
    except (ValueError, HPCStorageError, HPCConnectionError, HPCExecutionError) as exc:
        if isinstance(exc, HPCExecutionError):
            raise
        raise HPCExecutionError(str(exc)) from exc

    # With --parsable SLURM returns JOBID, optionally followed by ;cluster.
    job_id = output.strip().split(";", 1)[0].strip()
    if not _JOB_ID.fullmatch(job_id):
        raise HPCExecutionError(
            f"SLURM did not return a usable job ID. sbatch response: {output[:300]}"
        )
    return job_id


def _map_slurm_state(state: str) -> str | None:
    normalized = state.strip().upper().split()[0].rstrip("+") if state.strip() else ""
    if normalized in {"PENDING", "CONFIGURING", "REQUEUED", "RESIZING"}:
        return "Queued"
    if normalized in {"RUNNING", "COMPLETING", "SUSPENDED", "STAGE_OUT"}:
        return "Running"
    if normalized == "COMPLETED":
        return "Completed"
    if normalized == "CANCELLED":
        return "Cancelled"
    if normalized in {
        "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
        "BOOT_FAIL", "DEADLINE", "REVOKED", "SPECIAL_EXIT",
    }:
        return "Failed"
    return None


def get_slurm_job_status(job_id: str) -> str | None:
    """Return the current normalized status, or None while accounting catches up."""
    if not _JOB_ID.fullmatch(str(job_id)):
        raise ValueError("Invalid SLURM job ID.")

    quoted_job_id = quote(str(job_id))
    try:
        queue_state = run_ssh(
            f"squeue -h -j {quoted_job_id} -o '%T' 2>/dev/null || true"
        )
        if queue_state:
            mapped = _map_slurm_state(queue_state.splitlines()[0])
            if mapped:
                return mapped

        accounting = run_ssh(
            f"sacct -n -X -j {quoted_job_id} --format=State,ExitCode "
            "--parsable2 2>/dev/null | head -n 1 || true"
        )
    except HPCConnectionError as exc:
        raise HPCExecutionError(str(exc)) from exc

    if not accounting:
        return None

    state = accounting.split("|", 1)[0].strip()
    return _map_slurm_state(state)


def cancel_slurm_job(job_id: str) -> None:
    if not _JOB_ID.fullmatch(str(job_id)):
        raise ValueError("Invalid SLURM job ID.")
    try:
        run_ssh(f"scancel {quote(str(job_id))}")
    except HPCConnectionError as exc:
        raise HPCExecutionError(str(exc)) from exc
