from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from shlex import join as shell_join, quote
from typing import Sequence

from app.core.config import settings
from app.services.hpc.ssh import HPCConnectionError, run_ssh


_SEG_Y_SUFFIXES = {".sgy", ".segy"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_SLURM_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SAFE_SLURM_TIME = re.compile(r"^[A-Za-z0-9:._-]{1,32}$")
_SAFE_SLURM_MEMORY = re.compile(r"^[1-9][0-9]*[KMGTP]?$", re.IGNORECASE)


class HPCStorageError(Exception):
    pass


@dataclass(frozen=True)
class HPCDataFile:
    file_name: str
    relative_path: str
    size_bytes: int


def validate_segy_filename(filename: str) -> str:
    """Accept a SEG-Y basename only; never accept a directory or path."""
    if not filename or filename in {".", ".."}:
        raise ValueError("A SEG-Y filename is required.")
    if filename != filename.strip() or "/" in filename or "\\" in filename:
        raise ValueError("Only a filename is allowed; directories cannot be specified.")
    if any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise ValueError("The filename contains unsupported control characters.")
    if PurePosixPath(filename).name != filename:
        raise ValueError("Only a filename is allowed.")
    if PurePosixPath(filename).suffix.lower() not in _SEG_Y_SUFFIXES:
        raise ValueError("SEG-Y filenames must end in .sgy or .segy.")
    return filename


def validate_segy_relative_path(value: str) -> str:
    """Require exactly storage/data/<basename>; subdirectories are forbidden."""
    prefix = "storage/data/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError("SEG-Y files must be located directly in storage/data/.")
    filename = value[len(prefix):]
    validate_segy_filename(filename)
    return value


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(
            "Project and run identifiers may contain only letters, numbers, hyphens and underscores."
        )
    return value


def list_hpc_segy_files() -> list[HPCDataFile]:
    """List immediate SEG-Y files from the fixed HPC storage/data directory."""
    directory = quote(str(settings.hpc_data_dir))
    command = (
        f"find {directory} -maxdepth 1 -type f \\( -iname '*.sgy' -o -iname '*.segy' \\) "
        "-printf '%f\\t%s\\n' | sort -f"
    )
    try:
        output = run_ssh(command)
    except HPCConnectionError as exc:
        raise HPCStorageError(str(exc)) from exc

    files: list[HPCDataFile] = []
    for line in output.splitlines():
        if not line:
            continue
        parts = line.rsplit("\t", 1)
        if len(parts) != 2:
            continue
        filename, size_text = parts
        try:
            validate_segy_filename(filename)
            size_bytes = int(size_text)
            if size_bytes < 0:
                continue
        except (ValueError, TypeError):
            continue
        files.append(
            HPCDataFile(
                file_name=filename,
                relative_path=f"storage/data/{filename}",
                size_bytes=size_bytes,
            )
        )
    return files


@dataclass(frozen=True)
class RunStoragePaths:
    project_id: str
    run_id: str
    directory: PurePosixPath
    request_file: PurePosixPath
    batch_file: PurePosixPath
    status_file: PurePosixPath
    result_file: PurePosixPath
    stdout_pattern: PurePosixPath
    stderr_pattern: PurePosixPath


def get_run_storage_paths(project_id: str, run_id: str) -> RunStoragePaths:
    """Build the one permitted run-artifact layout; IDs are path-safe and opaque."""
    validate_run_id(project_id)
    validate_run_id(run_id)
    directory = settings.hpc_projects_dir / project_id / run_id
    return RunStoragePaths(
        project_id=project_id,
        run_id=run_id,
        directory=directory,
        request_file=directory / "request.json",
        batch_file=directory / "job.sbatch",
        status_file=directory / "status.json",
        result_file=directory / "result.json",
        stdout_pattern=directory / "stdout-%j.log",
        stderr_pattern=directory / "stderr-%j.log",
    )


def _safe_slurm_value(value: str, label: str, pattern: re.Pattern[str]) -> str:
    cleaned = str(value).strip()
    if not pattern.fullmatch(cleaned):
        raise ValueError(f"Invalid SLURM {label} value.")
    return cleaned


def build_sbatch_script(config, paths: RunStoragePaths, worker_argv: Sequence[str]) -> str:
    """Generate a batch script with SPI-managed log paths and a safely quoted argv."""
    job_name = _safe_slurm_value(config.job_name, "job name", _SAFE_SLURM_NAME)
    partition = _safe_slurm_value(config.partition, "partition", _SAFE_SLURM_NAME)
    time_limit = _safe_slurm_value(config.time, "time limit", _SAFE_SLURM_TIME)
    memory = _safe_slurm_value(config.memory, "memory", _SAFE_SLURM_MEMORY)

    if int(config.nodes) < 1 or int(config.tasks) < 1 or int(config.cpus) < 1:
        raise ValueError("SLURM nodes, tasks and CPUs must all be positive integers.")
    if not worker_argv or not all(isinstance(part, str) and part for part in worker_argv):
        raise ValueError("A worker command is required.")

    command = shell_join(list(worker_argv))
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --output={paths.stdout_pattern}",
        f"#SBATCH --error={paths.stderr_pattern}",
        f"#SBATCH --nodes={int(config.nodes)}",
        f"#SBATCH --ntasks={int(config.tasks)}",
        f"#SBATCH --cpus-per-task={int(config.cpus)}",
        f"#SBATCH --time={time_limit}",
        f"#SBATCH --mem={memory}",
        "",
        "set -euo pipefail",
        f"cd {quote(settings.hpc_root)}",
        command,
        "",
    ]
    return "\n".join(lines)


def ensure_remote_run_directory(paths: RunStoragePaths) -> None:
    directory = quote(str(paths.directory))
    try:
        run_ssh(f"mkdir -p {directory} && test -w {directory} && echo SPI_RUN_DIR_READY")
    except HPCConnectionError as exc:
        raise HPCStorageError(str(exc)) from exc


def read_run_log(paths: RunStoragePaths, stream: str, max_lines: int = 2000) -> str:
    """Read the tail of the actual SLURM log file stored in this run directory."""
    if stream not in {"stdout", "stderr"}:
        raise ValueError("Log stream must be stdout or stderr.")
    line_limit = max(1, min(int(max_lines), 10000))
    directory = quote(str(paths.directory))
    pattern = f"{stream}-*.log"
    command = (
        f"found=0; for file in {directory}/{pattern}; do "
        f"if [ -f \"$file\" ]; then found=1; tail -n {line_limit} \"$file\"; fi; "
        f"done; [ $found -eq 1 ] || true"
    )
    try:
        return run_ssh(command)
    except HPCConnectionError as exc:
        raise HPCStorageError(str(exc)) from exc
