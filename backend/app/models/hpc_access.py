from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HPCAccessLease(Base):
    """The single active Windows SSH-agent lease managed by SPI."""

    __tablename__ = "hpc_access_leases"

    # This is intentionally a singleton row for the personal-workstation model.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    public_key_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="connected")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
