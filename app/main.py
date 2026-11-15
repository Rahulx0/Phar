"""FastAPI application: medicine catalogue, batches, stock movements and ops endpoints."""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import service
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.logging_config import setup_logging
from app.models import Batch, Medicine, StockMovement
from app.schemas import (
    Adjustment,
    BatchCreate,
    BatchOut,
    ErrorBody,
    MedicineCreate,
    MedicineOut,
    MovementOut,
    StockOperation,
    StockReportRow,
)
from app.seed import seed_if_empty

logger = logging.getLogger("pharmacy.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    Base.metadata.create_all(bind=engine)  # simple, reproducible schema bootstrap
    if settings.seed_on_start:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    logger.info(
        "application started",
        extra={"event": "startup", "version": settings.app_version},
    )
    yield
    logger.info("application stopping", extra={"event": "shutdown"})


app = FastAPI(
    title="Pharmacy Management API",
    version=settings.app_version,
    description="Medicine, batch and stock-movement API with batch-level stock rules.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Structured request log: one JSON line per request with id + duration."""
    request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logging.getLogger("pharmacy.request").info(
        "request",
        extra={
            "event": "request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(service.DomainError)
async def domain_error_handler(_request: Request, exc: service.DomainError):
    """Domain rule violations -> machine-readable error codes."""
    body = ErrorBody(code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.get("/health", tags=["ops"])
def health():
    """Liveness: is the process up? (No DB access — that's /ready.)"""
    return {"status": "ok", "version": settings.app_version}


@app.get("/ready", tags=["ops"])
def ready(db: Session = Depends(get_db)):
    """Readiness: can this instance serve traffic, i.e. is the DB reachable?"""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed", extra={"event": "not_ready"})
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "down"})
    return {"status": "ready", "database": "up"}


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@app.get("/medicines", response_model=list[MedicineOut], tags=["catalogue"])
def list_medicines(db: Session = Depends(get_db)):
    return db.execute(select(Medicine).order_by(Medicine.id)).scalars().all()


@app.post("/medicines", response_model=MedicineOut, status_code=201, tags=["catalogue"])
def create_medicine(data: MedicineCreate, db: Session = Depends(get_db)):
    return service.create_medicine(db, data)


@app.get("/medicines/{medicine_id}", response_model=MedicineOut, tags=["catalogue"])
def get_medicine(medicine_id: int, db: Session = Depends(get_db)):
    medicine = db.get(Medicine, medicine_id)
    if medicine is None:
        raise service.MedicineNotFound
    return medicine


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------


def _batch_out(b: Batch) -> BatchOut:
    return BatchOut(
        id=b.id,
        medicine_id=b.medicine_id,
        batch_number=b.batch_number,
        expiry_date=b.expiry_date,
        quantity=b.quantity,
        created_at=b.created_at,
        expired=b.expiry_date <= date.today(),
    )


@app.get("/batches", response_model=list[BatchOut], tags=["batches"])
def list_batches(
    medicine_id: int | None = None,
    expired: bool | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Batch).order_by(Batch.id)
    if medicine_id is not None:
        stmt = stmt.where(Batch.medicine_id == medicine_id)
    batches = db.execute(stmt).scalars().all()
    if expired is not None:
        batches = [b for b in batches if (b.expiry_date <= date.today()) == expired]
    return [_batch_out(b) for b in batches]


@app.get("/batches/{batch_id}", response_model=BatchOut, tags=["batches"])
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise service.BatchNotFound
    return _batch_out(batch)


@app.post("/batches", response_model=BatchOut, status_code=201, tags=["batches"])
def create_batch(data: BatchCreate, db: Session = Depends(get_db)):
    batch = service.create_batch(db, data)
    return _batch_out(batch)


# ---------------------------------------------------------------------------
# Stock operations
# ---------------------------------------------------------------------------


@app.post("/purchases", response_model=MovementOut, status_code=201, tags=["stock"])
def purchase(op: StockOperation, db: Session = Depends(get_db)):
    return service.purchase(db, op.batch_id, op.quantity, op.note)


@app.post("/sales", response_model=MovementOut, status_code=201, tags=["stock"])
def sale(op: StockOperation, db: Session = Depends(get_db)):
    return service.sale(db, op.batch_id, op.quantity, op.note)


@app.post("/returns", response_model=MovementOut, status_code=201, tags=["stock"])
def return_stock(op: StockOperation, db: Session = Depends(get_db)):
    return service.record_return(db, op.batch_id, op.quantity, op.note)


@app.post("/adjustments", response_model=MovementOut, status_code=201, tags=["stock"])
def adjust_stock(data: Adjustment, db: Session = Depends(get_db)):
    return service.adjust(db, data.batch_id, data.counted_quantity, data.note)


@app.get("/movements", response_model=list[MovementOut], tags=["stock"])
def list_movements(
    batch_id: int | None = None,
    medicine_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(StockMovement).order_by(StockMovement.id.desc()).limit(limit)
    if batch_id is not None:
        stmt = stmt.where(StockMovement.batch_id == batch_id)
    if medicine_id is not None:
        stmt = stmt.where(StockMovement.medicine_id == medicine_id)
    return db.execute(stmt).scalars().all()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@app.get("/report/stock", response_model=list[StockReportRow], tags=["report"])
def stock_report(db: Session = Depends(get_db)):
    return service.stock_report(db)
