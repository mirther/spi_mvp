from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cluster_config_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PipelineNode(Base):
    __tablename__ = "pipeline_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    module_id: Mapped[str] = mapped_column(String(128), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PipelineConnection(Base):
    __tablename__ = "pipeline_connections"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_node_id", "target_node_id",
            name="uq_project_pipeline_connection",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ProjectRun(Base):
    __tablename__ = "project_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[str] = mapped_column(String(64), nullable=False)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class RunExecution(Base):
    """Execution metadata kept separate from the editable project snapshot.

    A separate table avoids a SQLite migration of the existing project_runs table.
    """

    __tablename__ = "run_executions"

    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("project_runs.id", ondelete="CASCADE"), primary_key=True
    )
    execution_mode: Mapped[str] = mapped_column(String(20), default="all", nullable=False)
    slurm_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_path: Mapped[str] = mapped_column(Text, nullable=False)
    batch_path: Mapped[str] = mapped_column(Text, nullable=False)
    stdout_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    stderr_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    submission_error: Mapped[str | None] = mapped_column(Text, nullable=True)
