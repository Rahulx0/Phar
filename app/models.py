"""SQLAlchemy models: Medicine, Batch and StockMovement (the audit trail)."""

import enum
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MovementType(enum.StrEnum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RETURN = "RETURN"
    ADJUSTMENT = "ADJUSTMENT"


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    form: Mapped[str] = mapped_column(String(50))  # tablet, capsule, syrup, sachet...
    reorder_level: Mapped[int] = mapped_column(default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batches: Mapped[list["Batch"]] = relationship(
        back_populates="medicine", cascade="all, delete-orphan"
    )


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("medicine_id", "batch_number"),
        # Database-level backstop: even if application validation were bypassed,
        # the schema itself refuses negative stock.
        CheckConstraint("quantity >= 0", name="ck_batch_quantity_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id"), index=True)
    batch_number: Mapped[str] = mapped_column(String(100))
    expiry_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    medicine: Mapped[Medicine] = relationship(back_populates="batches")


class StockMovement(Base):
    """Immutable audit record: every purchase, sale, return and adjustment."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id"), index=True)
    movement_type: Mapped[MovementType] = mapped_column(Enum(MovementType))
    # Positive magnitude for PURCHASE/SALE/RETURN; signed delta for ADJUSTMENT.
    quantity: Mapped[int] = mapped_column()
    balance_after: Mapped[int] = mapped_column()  # batch quantity after applying the movement
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
