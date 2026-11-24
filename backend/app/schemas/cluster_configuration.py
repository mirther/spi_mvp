from pydantic import BaseModel, ConfigDict, Field


class ClusterConfigurationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    job_name: str = Field(alias="jobName", min_length=1, max_length=128)
    partition: str = Field(min_length=1, max_length=128)
    nodes: int = Field(ge=1)
    tasks: int = Field(ge=1)
    cpus: int = Field(ge=1)
    time: str = Field(min_length=1, max_length=32)
    memory: str = Field(min_length=1, max_length=32)
