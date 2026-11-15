"""Domain rules from the assessment brief — the acceptance tests.

- A sale against an expired batch or insufficient batch quantity must fail
  WITHOUT changing stock.
- Negative/zero quantities are rejected at validation.
- Every purchase/sale/return/adjustment is recorded as a stock movement.
- Concurrent sales cannot oversell the same batch.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import select

from app import service
from app.models import Batch, MovementType, StockMovement
from tests.conftest import TestingSession, make_batch


def test_sale_from_expired_batch_fails_and_stock_is_unchanged(db, medicine):
    batch = make_batch(db, medicine.id, batch_number="EXP", days_to_expiry=-30, quantity=40)

    try:
        service.sale(db, batch.id, 5, note="should be rejected")
        raise AssertionError("sale from expired batch must raise")
    except service.ExpiredBatchError as exc:
        assert exc.code == "EXPIRED_BATCH"

    db.expire_all()
    assert db.get(Batch, batch.id).quantity == 40  # stock unchanged
    sales = (
        db.execute(select(StockMovement).where(StockMovement.movement_type == MovementType.SALE))
        .scalars()
        .all()
    )
    assert sales == []  # and nothing was recorded


def test_sale_with_insufficient_quantity_fails_and_stock_is_unchanged(db, medicine):
    batch = make_batch(db, medicine.id, batch_number="SMALL", quantity=3)

    try:
        service.sale(db, batch.id, 5, note="should be rejected")
        raise AssertionError("oversized sale must raise")
    except service.InsufficientStockError as exc:
        assert exc.code == "INSUFFICIENT_STOCK"

    db.expire_all()
    assert db.get(Batch, batch.id).quantity == 3
    assert (
        db.execute(select(StockMovement).where(StockMovement.movement_type == MovementType.SALE))
        .scalars()
        .all()
        == []
    )


def test_purchase_sale_and_return_are_all_recorded_as_movements(db, medicine):
    batch = make_batch(db, medicine.id, batch_number="AUDIT", quantity=10)

    service.purchase(db, batch.id, 5, "restock")  # 10 -> 15
    service.sale(db, batch.id, 4, "order-1")  # 15 -> 11
    service.record_return(db, batch.id, 2, "customer")  # 11 -> 13
    service.adjust(db, batch.id, 12, "cycle count")  # 13 -> 12

    movements = (
        db.execute(
            select(StockMovement)
            .where(StockMovement.batch_id == batch.id)
            .order_by(StockMovement.id)
        )
        .scalars()
        .all()
    )

    assert [(m.movement_type, m.quantity, m.balance_after) for m in movements] == [
        (MovementType.PURCHASE, 10, 10),  # opening stock from make_batch
        (MovementType.PURCHASE, 5, 15),
        (MovementType.SALE, 4, 11),
        (MovementType.RETURN, 2, 13),
        (MovementType.ADJUSTMENT, -1, 12),  # signed delta
    ]
    db.expire_all()
    assert db.get(Batch, batch.id).quantity == 12


def test_unknown_batch_rejected(db):
    try:
        service.sale(db, batch_id=9999, quantity=1, note=None)
        raise AssertionError("unknown batch must raise")
    except service.BatchNotFound:
        pass


def test_concurrent_sales_cannot_oversell(db, medicine):
    """10 concurrent sales of 1 unit against a batch holding 5 units:
    exactly 5 succeed, the batch drains to exactly 0, and 5 SALE movements
    are recorded. Row locking (SELECT ... FOR UPDATE) serialises the buyers.

    Requires PostgreSQL: SQLite ignores FOR UPDATE and its read snapshot
    lets every buyer see the pre-sale quantity, so the guard cannot be
    exercised there. CI runs this suite against a Postgres service container;
    the Docker demo stack (Postgres) reproduces it via `make demo-oversell`.
    """
    if db.get_bind().dialect.name == "sqlite":
        pytest.skip("SELECT ... FOR UPDATE requires PostgreSQL (runs in CI / Docker stack)")

    batch = make_batch(db, medicine.id, batch_number="RACE", quantity=5)

    def attempt(_):
        session = TestingSession()
        try:
            service.sale(session, batch.id, 1, note="concurrent")
            return True
        except service.InsufficientStockError:
            return False
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))

    assert sum(results) == 5, "only the available stock can be sold"
    db.expire_all()
    assert db.get(Batch, batch.id).quantity == 0
    sales = (
        db.execute(
            select(StockMovement).where(
                StockMovement.batch_id == batch.id, StockMovement.movement_type == MovementType.SALE
            )
        )
        .scalars()
        .all()
    )
    assert len(sales) == 5


def test_expired_batch_boundary_is_exclusive(db, medicine):
    """A batch expiring today is already unsellable (expiry_date <= today)."""
    batch = make_batch(db, medicine.id, batch_number="TODAY", days_to_expiry=0, quantity=10)
    assert batch.expiry_date == date.today()
    try:
        service.sale(db, batch.id, 1, note=None)
        raise AssertionError("batch expiring today must not be sellable")
    except service.ExpiredBatchError:
        pass


def test_near_expiry_batch_is_still_sellable(db, medicine):
    batch = make_batch(db, medicine.id, batch_number="SOON", days_to_expiry=30, quantity=10)
    movement = service.sale(db, batch.id, 10, note="clearing near-expiry stock")
    assert movement.balance_after == 0
