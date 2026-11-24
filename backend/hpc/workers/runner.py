#!/usr/bin/env python3
"""SLURM entry point for an SPI Run All request."""

import argparse
import json
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPI_ROOT = Path(__file__).resolve().parents[1]
if str(SPI_ROOT) not in sys.path:
    sys.path.insert(0, str(SPI_ROOT))

from workers.runtime.execution_context import ExecutionContext
from workers.runtime.pipeline_executor import execute_pipeline


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_request(request_path: Path) -> dict[str, Any]:
    with request_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def make_context(request: dict[str, Any], request_path: Path) -> ExecutionContext:
    storage = request["storage"]
    project_id = str(request["project_id"])
    run_id = str(request["run_id"])

    expected_data_dir = (SPI_ROOT / "storage" / "data").resolve()
    expected_run_dir = (SPI_ROOT / "projects" / project_id / run_id).resolve()
    data_directory = Path(storage["data_directory"]).resolve()
    run_directory = Path(storage["run_directory"]).resolve()

    # Keep the filesystem contract fixed even if a request is manually edited.
    if data_directory != expected_data_dir:
        raise ValueError("Run request points outside the fixed storage/data directory.")
    if run_directory != expected_run_dir:
        raise ValueError("Run request points outside its assigned projects/<project>/<run> directory.")
    if not run_directory.is_dir():
        raise FileNotFoundError(f"Run directory does not exist: {run_directory}")

    return ExecutionContext(
        project_id=project_id,
        project_name=str(request.get("project_name", project_id)),
        run_id=run_id,
        spi_root=SPI_ROOT,
        data_directory=data_directory,
        run_directory=run_directory,
        request_path=request_path.resolve(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute an SPI pipeline request under SLURM.")
    parser.add_argument("--request", required=True, help="Path to the run's request.json")
    args = parser.parse_args()

    request_path = Path(args.request).resolve()
    request = load_request(request_path)
    context = make_context(request, request_path)

    write_json_atomic(
        context.status_path,
        {
            "status": "Running",
            "project_id": context.project_id,
            "run_id": context.run_id,
            "started_at": utc_now(),
        },
    )
    print(
        f"[SPI] Starting Run All: project={context.project_id}, run={context.run_id}",
        flush=True,
    )

    try:
        result = execute_pipeline(request, context)
        completed_at = utc_now()
        result["completed_at"] = completed_at
        write_json_atomic(context.result_path, result)
        write_json_atomic(
            context.status_path,
            {
                "status": "Completed",
                "project_id": context.project_id,
                "run_id": context.run_id,
                "completed_at": completed_at,
            },
        )
        print("[SPI] Pipeline completed successfully.", flush=True)
        return 0
    except Exception as exc:
        failed_at = utc_now()
        traceback_text = traceback.format_exc()
        print(traceback_text, file=sys.stderr, flush=True)
        try:
            write_json_atomic(
                context.result_path,
                {
                    "status": "Failed",
                    "project_id": context.project_id,
                    "run_id": context.run_id,
                    "failed_at": failed_at,
                    "error": str(exc),
                    "traceback": traceback_text,
                },
            )
            write_json_atomic(
                context.status_path,
                {
                    "status": "Failed",
                    "project_id": context.project_id,
                    "run_id": context.run_id,
                    "failed_at": failed_at,
                    "error": str(exc),
                },
            )
        except Exception:
            traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
