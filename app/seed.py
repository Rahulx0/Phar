"""Synthetic seed data (idempotent: only runs when the catalogue is empty).

Deliberately includes an expired batch, a near-expiry batch and a
below-reorder-level medicine so every report flag and domain rule can be
demonstrated immediately after a fresh deployment.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Batch, Medicine, MovementType, StockMovement

logger = logging.getLogger("pharmacy.seed")


def seed_if_empty(db: Session) -> None:
    # Auto-begin style: the emptiness check opens a transaction implicitly,
    # inserts reuse it, and we commit explicitly. Works whether or not the
    # caller's session already holds a transaction — a separate db.begin()
    # here would raise "transaction already begun".
    try:
        count = db.execute(select(func.count()).select_from(Medicine)).scalar_one()
        if count > 0:
            db.rollback()  # release the read transaction; nothing to seed
            return
        _insert_catalogue(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info(
        "seeded synthetic catalogue",
        extra={"event": "seed", "medicines": 5},
    )


def _insert_catalogue(db: Session) -> None:
    today = date.today()
    expired = today - timedelta(days=30)
    near_expiry = today + timedelta(days=45)
    ok = today + timedelta(days=400)

    catalogue = [
        (
            "Paracetamol 500mg",
            "tablet",
            10,
            [("PARA-2027", ok, 100), ("PARA-2025-EXP", expired, 40)],
        ),
        ("Amoxicillin 500mg", "capsule", 15, [("AMOX-2026-N", near_expiry, 8)]),
        ("Ibuprofen 400mg", "tablet", 10, [("IBU-2028", ok, 120)]),
        ("Cough Syrup 100ml", "syrup", 5, [("SYR-2026-N", near_expiry, 25)]),
        ("ORS Sachet", "sachet", 10, [("ORS-2027", ok, 60)]),
    ]

    for name, form, reorder_level, batches in catalogue:
        medicine = Medicine(name=name, form=form, reorder_level=reorder_level)
        db.add(medicine)
        db.flush()
        for batch_number, expiry_date, quantity in batches:
            batch = Batch(
                medicine_id=medicine.id,
                batch_number=batch_number,
                expiry_date=expiry_date,
                quantity=quantity,
            )
            db.add(batch)
            db.flush()
            db.add(
                StockMovement(
                    batch_id=batch.id,
                    medicine_id=medicine.id,
                    movement_type=MovementType.PURCHASE,
                    quantity=quantity,
                    balance_after=quantity,
                    note="seed opening stock",
                )
            )
