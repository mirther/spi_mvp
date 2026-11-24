from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.cluster_configuration import ClusterConfiguration
from app.schemas.cluster_configuration import ClusterConfigurationPayload

router = APIRouter()

# Legacy database columns are retained for compatibility with existing spi.db files.
# Their values are no longer configurable or used: every run gets generated paths.
MANAGED_LOG_MARKER = "managed-by-spi"


def to_dict(config: ClusterConfiguration) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "jobName": config.job_name,
        "partition": config.partition,
        "nodes": config.nodes,
        "tasks": config.tasks,
        "cpus": config.cpus,
        "time": config.time,
        "memory": config.memory,
    }


@router.get("")
def list_cluster_configurations(db: Session = Depends(get_db)):
    configs = db.scalars(
        select(ClusterConfiguration).order_by(ClusterConfiguration.name, ClusterConfiguration.id)
    ).all()
    return [to_dict(config) for config in configs]


@router.put("")
def replace_cluster_configurations(
    payload: list[ClusterConfigurationPayload],
    db: Session = Depends(get_db),
):
    ids = [config.id for config in payload]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Cluster configuration IDs must be unique.")

    db.execute(delete(ClusterConfiguration))
    db.add_all([
        ClusterConfiguration(
            id=config.id,
            name=config.name,
            job_name=config.job_name,
            partition=config.partition,
            # Keep the legacy NOT NULL columns populated, but never use them as paths.
            stdout=MANAGED_LOG_MARKER,
            stderr=MANAGED_LOG_MARKER,
            nodes=config.nodes,
            tasks=config.tasks,
            cpus=config.cpus,
            time=config.time,
            memory=config.memory,
        )
        for config in payload
    ])
    db.commit()
    configs = db.scalars(
        select(ClusterConfiguration).order_by(ClusterConfiguration.name, ClusterConfiguration.id)
    ).all()
    return [to_dict(config) for config in configs]
