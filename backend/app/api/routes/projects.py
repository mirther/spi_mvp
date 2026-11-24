from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.cluster_configuration import ClusterConfiguration
from app.models.project import PipelineConnection, PipelineNode, Project, ProjectRun, RunExecution
from app.schemas.project import ProjectPayload
from app.services.pipeline_order import get_execution_order
from app.services.hpc.execution import (
    HPCExecutionError,
    build_run_request,
    cancel_slurm_job,
    get_slurm_job_status,
    submit_run,
)
from app.services.hpc.storage import (
    HPCStorageError,
    get_run_storage_paths,
    read_run_log,
    validate_segy_relative_path,
)

router = APIRouter()


def validate_pipeline(payload: ProjectPayload) -> None:
    node_ids = [node.id for node in payload.nodes]
    if len(node_ids) != len(set(node_ids)):
        raise HTTPException(status_code=422, detail="Pipeline node IDs must be unique.")

    # Enforce the fixed SEG-Y directory when project definitions are saved.
    # This is storage-boundary enforcement, not a Run All preflight pass.
    for node in payload.nodes:
        module_id = node.module_id.lower().replace("_", "").replace("-", "")
        if module_id in {"segyin", "segyout"}:
            # Empty values are allowed while a newly-added module is unconfigured.
            # Any value that is present must still stay inside the fixed storage/data directory.
            stored_path = str(node.parameters.get("path", "")).strip()
            if stored_path:
                try:
                    validate_segy_relative_path(stored_path)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

    node_id_set = set(node_ids)
    edge_pairs: set[tuple[str, str]] = set()

    for connection in payload.connections:
        source = connection.source_node_id
        target = connection.target_node_id

        if source == target:
            raise HTTPException(status_code=422, detail="A module cannot connect to itself.")
        if source not in node_id_set or target not in node_id_set:
            raise HTTPException(
                status_code=422,
                detail="Every connection must reference modules in the same project.",
            )
        pair = (source, target)
        if pair in edge_pairs:
            raise HTTPException(status_code=422, detail="Duplicate pipeline connections are not allowed.")
        edge_pairs.add(pair)

    # Graph integrity is enforced when the project is saved, not as a Run All preflight.
    try:
        get_execution_order(
            node_ids,
            [(edge.source_node_id, edge.target_node_id) for edge in payload.connections],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Pipeline connections cannot contain a cycle.") from exc


def project_to_dict(db: Session, project: Project) -> dict:
    nodes = db.scalars(
        select(PipelineNode)
        .where(PipelineNode.project_id == project.id)
        .order_by(PipelineNode.sort_order)
    ).all()
    connections = db.scalars(
        select(PipelineConnection)
        .where(PipelineConnection.project_id == project.id)
        .order_by(PipelineConnection.sort_order)
    ).all()
    runs = db.scalars(
        select(ProjectRun)
        .where(ProjectRun.project_id == project.id)
        .order_by(ProjectRun.sort_order.desc(), ProjectRun.started_at.desc())
    ).all()
    run_ids = [run.id for run in runs]
    executions = {}
    if run_ids:
        executions = {
            item.run_id: item
            for item in db.scalars(
                select(RunExecution).where(RunExecution.run_id.in_(run_ids))
            ).all()
        }
    execution_order = get_execution_order(
        [node.id for node in nodes],
        [(connection.source_node_id, connection.target_node_id) for connection in connections],
    )

    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "clusterConfigId": project.cluster_config_id,
        "nodes": [
            {
                "id": node.id,
                "moduleId": node.module_id,
                "x": node.x,
                "y": node.y,
                "parameters": node.parameters or {},
            }
            for node in nodes
        ],
        "connections": [
            {
                "id": connection.id,
                "sourceNodeId": connection.source_node_id,
                "targetNodeId": connection.target_node_id,
            }
            for connection in connections
        ],
        "executionOrder": execution_order,
        "runs": [
            {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "startedAt": run.started_at,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "slurmJobId": executions.get(run.id).slurm_job_id if executions.get(run.id) else None,
                "submissionError": executions.get(run.id).submission_error if executions.get(run.id) else None,
            }
            for run in runs
        ],
    }


def write_project_snapshot(db: Session, project: Project, payload: ProjectPayload) -> None:
    validate_pipeline(payload)

    project.name = payload.name
    project.description = payload.description
    project.cluster_config_id = payload.cluster_config_id

    # Replace the project's editable pipeline snapshot atomically.
    db.execute(delete(PipelineConnection).where(PipelineConnection.project_id == project.id))
    db.execute(delete(PipelineNode).where(PipelineNode.project_id == project.id))
    db.flush()

    db.add_all([
        PipelineNode(
            id=node.id,
            project_id=project.id,
            module_id=node.module_id,
            x=node.x,
            y=node.y,
            parameters=node.parameters,
            sort_order=index,
        )
        for index, node in enumerate(payload.nodes)
    ])
    db.add_all([
        PipelineConnection(
            id=connection.id,
            project_id=project.id,
            source_node_id=connection.source_node_id,
            target_node_id=connection.target_node_id,
            sort_order=index,
        )
        for index, connection in enumerate(payload.connections)
    ])
    # Run records are managed by execution endpoints, not editable pipeline snapshots.
    # This prevents autosaving a project from replacing or deleting run history.


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).order_by(Project.created_at, Project.id)).all()
    return [project_to_dict(db, project) for project in projects]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectPayload, db: Session = Depends(get_db)):
    if db.get(Project, payload.id) is not None:
        raise HTTPException(status_code=409, detail="A project with this ID already exists.")

    project = Project(
        id=payload.id,
        name=payload.name,
        description=payload.description,
        cluster_config_id=payload.cluster_config_id,
    )
    db.add(project)
    try:
        db.flush()
        write_project_snapshot(db, project, payload)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not save project because of a database conflict.") from exc

    db.refresh(project)
    return project_to_dict(db, project)


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project_to_dict(db, project)


@router.put("/{project_id}")
def update_project(project_id: str, payload: ProjectPayload, db: Session = Depends(get_db)):
    if payload.id != project_id:
        raise HTTPException(status_code=422, detail="Project ID in the URL and request body must match.")

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    try:
        write_project_snapshot(db, project, payload)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not update project because of a database conflict.") from exc

    db.refresh(project)
    return project_to_dict(db, project)


def run_to_dict(run: ProjectRun, execution: RunExecution | None) -> dict:
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "startedAt": run.started_at,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "slurmJobId": execution.slurm_job_id if execution else None,
        "submissionError": execution.submission_error if execution else None,
    }


@router.post("/{project_id}/runs", status_code=status.HTTP_201_CREATED)
def launch_run_all(project_id: str, db: Session = Depends(get_db)):
    """Snapshot the saved graph, order it, create one run and submit one SLURM job."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    cluster = db.get(ClusterConfiguration, project.cluster_config_id) if project.cluster_config_id else None
    if cluster is None:
        raise HTTPException(status_code=409, detail="Select a cluster configuration before running the pipeline.")

    nodes = db.scalars(
        select(PipelineNode).where(PipelineNode.project_id == project_id).order_by(PipelineNode.sort_order)
    ).all()
    connections = db.scalars(
        select(PipelineConnection).where(PipelineConnection.project_id == project_id).order_by(PipelineConnection.sort_order)
    ).all()
    if not nodes:
        raise HTTPException(status_code=409, detail="The project has no modules to run.")

    try:
        execution_order = get_execution_order(
            [node.id for node in nodes],
            [(edge.source_node_id, edge.target_node_id) for edge in connections],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="The saved pipeline graph cannot be ordered.") from exc

    run_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    paths = get_run_storage_paths(project_id, run_id)
    run_index = db.scalar(
        select(func.coalesce(func.max(ProjectRun.sort_order), -1)).where(ProjectRun.project_id == project_id)
    ) + 1
    run = ProjectRun(
        id=run_id,
        project_id=project_id,
        name=f"Run All · {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        status="Queued",
        started_at=started_at,
        sort_order=run_index,
    )
    execution = RunExecution(
        run_id=run_id,
        execution_mode="all",
        request_path=str(paths.request_file),
        batch_path=str(paths.batch_file),
        stdout_pattern=str(paths.stdout_pattern),
        stderr_pattern=str(paths.stderr_pattern),
    )
    db.add(run)
    db.add(execution)
    db.commit()

    request_document = build_run_request(
        project_id=project_id,
        run_id=run_id,
        project_name=project.name,
        execution_order=execution_order,
        nodes=nodes,
        connections=connections,
    )

    try:
        slurm_job_id = submit_run(paths, cluster, request_document)
        execution.slurm_job_id = slurm_job_id
        db.commit()
    except (HPCExecutionError, ValueError) as exc:
        run.status = "Failed"
        execution.submission_error = str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=f"Could not submit the SLURM job: {exc}") from exc

    db.refresh(run)
    db.refresh(execution)
    return run_to_dict(run, execution)


@router.get("/{project_id}/runs/{run_id}")
def get_run_status(project_id: str, run_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    run = db.get(ProjectRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found.")
    execution = db.get(RunExecution, run_id)
    if execution and execution.slurm_job_id and run.status in {"Queued", "Running"}:
        try:
            current_status = get_slurm_job_status(execution.slurm_job_id)
        except (HPCExecutionError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if current_status:
            run.status = current_status
            db.commit()
    return run_to_dict(run, execution)


@router.post("/{project_id}/runs/{run_id}/cancel")
def cancel_run(project_id: str, run_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    run = db.get(ProjectRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found.")
    execution = db.get(RunExecution, run_id)
    if not execution or not execution.slurm_job_id:
        raise HTTPException(status_code=409, detail="This run has no submitted SLURM job.")
    if run.status not in {"Queued", "Running"}:
        raise HTTPException(status_code=409, detail="This run is already finished.")
    try:
        cancel_slurm_job(execution.slurm_job_id)
    except (HPCExecutionError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    run.status = "Cancelled"
    db.commit()
    return run_to_dict(run, execution)


@router.delete("/{project_id}/runs", status_code=status.HTTP_204_NO_CONTENT)
def clear_run_history(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    runs = db.scalars(select(ProjectRun).where(ProjectRun.project_id == project_id)).all()
    active_jobs: list[str] = []
    run_directories = []
    for run in runs:
        execution = db.get(RunExecution, run.id)
        if run.status in {"Queued", "Running"} and execution and execution.slurm_job_id:
            try:
                latest_status = get_slurm_job_status(execution.slurm_job_id)
            except HPCExecutionError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if latest_status:
                run.status = latest_status
        elif run.status in {"Queued", "Running"} and (not execution or not execution.slurm_job_id):
            # Legacy placeholder or incomplete submission record with no real SLURM job.
            run.status = "Failed"
        if run.status in {"Queued", "Running"}:
            active_jobs.append(execution.slurm_job_id if execution and execution.slurm_job_id else run.id)
        run_directories.append(get_run_storage_paths(project_id, run.id).directory)

    if active_jobs:
        raise HTTPException(
            status_code=409,
            detail="Cancel or finish active jobs before clearing run history.",
        )

    if run_directories:
        from shlex import quote
        from app.services.hpc.ssh import HPCConnectionError, run_ssh
        command = "rm -rf -- " + " ".join(quote(str(path)) for path in run_directories)
        try:
            run_ssh(command)
        except HPCConnectionError as exc:
            raise HTTPException(status_code=503, detail=f"Could not remove HPC run files: {exc}") from exc

    for run in runs:
        execution = db.get(RunExecution, run.id)
        if execution is not None:
            db.delete(execution)
        db.delete(run)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/runs/{run_id}/logs")
def get_run_logs(project_id: str, run_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    run = db.get(ProjectRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found.")

    execution = db.get(RunExecution, run_id)
    if execution and execution.submission_error and not execution.slurm_job_id:
        stdout = ""
        stderr = ""
    else:
        try:
            paths = get_run_storage_paths(project_id, run_id)
            stdout = read_run_log(paths, "stdout")
            stderr = read_run_log(paths, "stderr")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HPCStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "stdout": stdout,
        "stderr": stderr,
        "source": "HPC run log files",
        "slurmJobId": execution.slurm_job_id if execution else None,
        "submissionError": execution.submission_error if execution else None,
    }


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
