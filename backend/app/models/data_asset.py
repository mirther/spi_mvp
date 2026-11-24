from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DataAsset(Base):
    __tablename__ = "data_assets"

    # Identification
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Original file information
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    remote_path: Mapped[str] = mapped_column(
        String(1024),
        unique=True,
        nullable=False,
    )

    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # SEG-Y metadata
    trace_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    declared_samples_per_trace: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    sample_interval_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    sample_format_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Import and inspection status
    status: Mapped[str] = mapped_column(
        String(20),
        default="uploaded",
        nullable=False,
    )

    inspection_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )