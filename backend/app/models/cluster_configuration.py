from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClusterConfiguration(Base):
    __tablename__ = "cluster_configurations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_name: Mapped[str] = mapped_column(String(128), nullable=False)
    partition: Mapped[str] = mapped_column(String(128), nullable=False)
    stdout: Mapped[str] = mapped_column(String(255), nullable=False)
    stderr: Mapped[str] = mapped_column(String(255), nullable=False)
    nodes: Mapped[int] = mapped_column(Integer, nullable=False)
    tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    cpus: Mapped[int] = mapped_column(Integer, nullable=False)
    time: Mapped[str] = mapped_column(String(32), nullable=False)
    memory: Mapped[str] = mapped_column(String(32), nullable=False)
