"""Seeding: idempotency and the demo-fixtures it must always provide.

Regression coverage for the startup path the API tests bypass (they run
without the app lifespan): seed_if_empty must survive a session with no
implicit transaction and must be safe to call twice.
"""

from sqlalchemy import func, select

from app.models import Batch, Medicine, StockMovement
from app.seed import seed_if_empty
from tests.conftest import TestingSession  # test engine, not the app's default DB


def _counts(session):
    return {
        "medicines": session.execute(select(func.count()).select_from(Medicine)).scalar_one(),
        "batches": session.execute(select(func.count()).select_from(Batch)).scalar_one(),
        "movements": session.execute(select(func.count()).select_from(StockMovement)).scalar_one(),
    }


def test_seed_is_idempotent_and_provides_demo_fixtures(clean_tables):
    # fresh SessionLocal like the app lifespan uses (no leftover transaction)
    session = TestingSession()
    try:
        seed_if_empty(session)
        first = _counts(session)
        assert first["medicines"] == 5
        assert first["batches"] == 6
        assert first["movements"] == first["batches"]  # one opening PURCHASE per batch

        # second call must be a no-op (idempotent)
        seed_if_empty(session)
        assert _counts(session) == first
    finally:
        session.close()

    # demo fixtures exist: exactly one expired batch and at least one
    # near-expiry batch so the domain rules can be demonstrated on a
    # fresh deployment
    session = TestingSession()
    try:
        from datetime import date, timedelta

        today = date.today()
        batches = session.execute(select(Batch)).scalars().all()
        assert sum(1 for b in batches if b.expiry_date <= today) == 1
        soon = today + timedelta(days=90)
        assert any(today < b.expiry_date <= soon for b in batches)
    finally:
        session.close()
