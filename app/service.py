"""Domain service: stock rules and the auditable stock movements.

Every quantity change happens here, inside one transaction, with the batch
row locked (SELECT ... FOR UPDATE) so concurrent sales of the same batch
serialise and can never oversell. Each change writes a StockMovement record
with the resulting balance, giving a complete audit trail.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Medicine, MovementType, StockMovement

logger = logging.getLogger("pharmacy.service")

EXPIRING_SOON_DAYS = 90


# ---------------------------------------------------------------------------
# Domain errors -> mapped to HTTP responses in main.py
# ---------------------------------------------------------------------------


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"
    message = "Domain rule violated"


class BatchNotFound(DomainError):
    status_code = 404
    code = "BATCH_NOT_FOUND"
    message = "Batch does not exist"


class MedicineNotFound(DomainError):
    status_code = 404
    code = "MEDICINE_NOT_FOUND"
    message = "Medicine does not exist"


class ExpiredBatchError(DomainError):
    status_code = 422
    code = "EXPIRED_BATCH"
    message = "Batch is expired; sales from expired batches are not allowed"


class InsufficientStockError(DomainError):
    status_code = 422
    code = "INSUFFICIENT_STOCK"
    message = "Batch does not have enough quantity to fulfil this sale"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _locked_batch(db: Session, batch_id: int) -> Batch:
    """Fetch a batch and lock its row for the duration of the transaction.

    with_for_update() emits SELECT ... FOR UPDATE on PostgreSQL, so two
    concurrent sales of the same batch cannot both read the same quantity.
    (SQLite ignores the clause; its single-writer lock gives the same
    guarantee for tests.)
    """
    batch = db.execute(
        select(Batch).where(Batch.id == batch_id).with_for_update()
    ).scalar_one_or_none()
    if batch is None:
        raise BatchNotFound
    return batch


def _record(
    db: Session, batch: Batch, movement_type: MovementType, quantity: int, note: str | None
) -> StockMovement:
    movement = StockMovement(
        batch_id=batch.id,
        medicine_id=batch.medicine_id,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=batch.quantity,
        note=note,
    )
    db.add(movement)
    db.flush()  # assign movement.id so the caller can return it
    return movement


# ---------------------------------------------------------------------------
# Operations (each: validate -> mutate -> audit, in one transaction)
# ---------------------------------------------------------------------------


def create_medicine(db: Session, data) -> Medicine:
    with db.begin():
        medicine = Medicine(name=data.name, form=data.form, reorder_level=data.reorder_level)
        db.add(medicine)
        db.flush()
    return medicine


def create_batch(db: Session, data) -> Batch:
    with db.begin():
        if db.get(Medicine, data.medicine_id) is None:
            raise MedicineNotFound
        batch = Batch(
            medicine_id=data.medicine_id,
            batch_number=data.batch_number,
            expiry_date=data.expiry_date,
            quantity=data.quantity,
        )
        db.add(batch)
        db.flush()
        if data.quantity > 0:
            _record(db, batch, MovementType.PURCHASE, data.quantity, "opening stock")
    return batch


def purchase(db: Session, batch_id: int, quantity: int, note: str | None) -> StockMovement:
    with db.begin():
        batch = _locked_batch(db, batch_id)
        batch.quantity += quantity
        movement = _record(db, batch, MovementType.PURCHASE, quantity, note)
    logger.info(
        "stock.purchase",
        extra={"event": "stock.purchase", "batch_id": batch_id, "quantity": quantity},
    )
    return movement


def sale(db: Session, batch_id: int, quantity: int, note: str | None) -> StockMovement:
    with db.begin():
        batch = _locked_batch(db, batch_id)

        if batch.expiry_date <= date.today():
            raise ExpiredBatchError
        if batch.quantity < quantity:
            raise InsufficientStockError

        batch.quantity -= quantity
        movement = _record(db, batch, MovementType.SALE, quantity, note)
    logger.info(
        "stock.sale", extra={"event": "stock.sale", "batch_id": batch_id, "quantity": quantity}
    )
    return movement


def record_return(db: Session, batch_id: int, quantity: int, note: str | None) -> StockMovement:
    """Customer return: stock comes back onto the shelf (audit-recorded)."""
    with db.begin():
        batch = _locked_batch(db, batch_id)
        batch.quantity += quantity
        movement = _record(db, batch, MovementType.RETURN, quantity, note)
    logger.info(
        "stock.return", extra={"event": "stock.return", "batch_id": batch_id, "quantity": quantity}
    )
    return movement


def adjust(db: Session, batch_id: int, counted_quantity: int, note: str | None) -> StockMovement:
    """Cycle count: set the batch to the physically counted quantity.

    The delta (which may be negative) is recorded so the audit trail
    reconciles the book quantity with the shelf quantity.
    """
    with db.begin():
        batch = _locked_batch(db, batch_id)
        delta = counted_quantity - batch.quantity
        batch.quantity = counted_quantity
        movement = _record(db, batch, MovementType.ADJUSTMENT, delta, note)
    logger.info(
        "stock.adjustment",
        extra={"event": "stock.adjustment", "batch_id": batch_id, "delta": delta},
    )
    return movement


def stock_report(db: Session) -> list[dict]:
    """Current stock position per medicine with expiry and reorder flags."""
    today = date.today()
    soon = today + timedelta(days=EXPIRING_SOON_DAYS)

    report: list[dict] = []
    medicines = db.execute(select(Medicine).order_by(Medicine.id)).scalars().all()
    for medicine in medicines:
        batches = db.execute(select(Batch).where(Batch.medicine_id == medicine.id)).scalars().all()
        report.append(
            {
                "medicine_id": medicine.id,
                "name": medicine.name,
                "total_quantity": sum(b.quantity for b in batches),
                "batch_count": len(batches),
                "expired_quantity": sum(b.quantity for b in batches if b.expiry_date <= today),
                "expiring_soon_quantity": sum(
                    b.quantity for b in batches if today < b.expiry_date <= soon
                ),
                "below_reorder_level": sum(b.quantity for b in batches) < medicine.reorder_level,
                "reorder_level": medicine.reorder_level,
            }
        )
    return report
