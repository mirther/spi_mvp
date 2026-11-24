from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PipelineNodePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=64)
    module_id: str = Field(alias="moduleId", min_length=1, max_length=128)
    x: float
    y: float
    parameters: dict[str, str] = Field(default_factory=dict)


class PipelineConnectionPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=64)
    source_node_id: str = Field(alias="sourceNodeId", min_length=1, max_length=64)
    target_node_id: str = Field(alias="targetNodeId", min_length=1, max_length=64)


class ProjectRunPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    status: Literal["Queued", "Running", "Completed", "Failed", "Cancelled"]
    started_at: str = Field(alias="startedAt", min_length=1, max_length=64)
    stdout: str | None = None
    stderr: str | None = None


class ProjectPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    nodes: list[PipelineNodePayload] = Field(default_factory=list)
    connections: list[PipelineConnectionPayload] = Field(default_factory=list)
    cluster_config_id: str = Field(alias="clusterConfigId", default="", max_length=64)
    runs: list[ProjectRunPayload] = Field(default_factory=list)
