"""Test fixtures.

The test engine honours DATABASE_URL, so the same suite runs against:
- SQLite file (default, zero-setup local runs)
- PostgreSQL (the CI job provides a Postgres service container and exports
  DATABASE_URL — this is where SELECT ... FOR UPDATE is truly exercised).
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models import Batch, Medicine, StockMovement

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test_local.db")

if DATABASE_URL.startswith("sqlite"):
    # timeout=30: concurrent writers queue instead of failing with
    # "database is locked" (mirrors row-lock serialisation on PostgreSQL).
    connect_args = {"check_same_thread": False, "timeout": 30}
else:
    connect_args = {}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    # Wipe data between tests; deletion order respects foreign keys.
    with engine.begin() as conn:
        for table in (StockMovement.__table__, Batch.__table__, Medicine.__table__):
            conn.execute(table.delete())


@pytest.fixture
def db():
    session = TestingSession()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    fastapi_app.dependency_overrides[get_db] = override_get_db
    # Note: TestClient used without a context manager on purpose — the app
    # lifespan (schema bootstrap + seeding) is deployment behaviour, not
    # under test here; each test builds its own data.
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def medicine(db):
    from app import service
    from app.schemas import MedicineCreate

    return service.create_medicine(db, MedicineCreate(name="Test Medicine", form="tablet"))


def make_batch(db, medicine_id, batch_number="B-1", days_to_expiry=365, quantity=10):
    from datetime import date, timedelta

    from app import service
    from app.schemas import BatchCreate

    return service.create_batch(
        db,
        BatchCreate(
            medicine_id=medicine_id,
            batch_number=batch_number,
            expiry_date=date.today() + timedelta(days=days_to_expiry),
            quantity=quantity,
        ),
    )
