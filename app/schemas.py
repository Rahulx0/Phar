"""Pydantic schemas: request validation and response shapes.

Validation rules enforced here (the domain brief):
- quantities must be positive integers (negative/zero rejected with 422)
- batch numbers are required and non-empty
- expiry dates are required ISO dates
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class MedicineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    form: str = Field(min_length=1, max_length=50)
    reorder_level: int = Field(default=10, ge=0)


class BatchCreate(BaseModel):
    medicine_id: int
    batch_number: str = Field(min_length=1, max_length=100)
    expiry_date: date
    quantity: int = Field(default=0, ge=0)  # opening stock; negative rejected


class StockOperation(BaseModel):
    """Shared shape for purchase / sale / return requests."""

    batch_id: int
    quantity: int = Field(gt=0, description="Must be a positive integer")
    note: str | None = Field(default=None, max_length=300)


class Adjustment(BaseModel):
    """Cycle-count adjustment: set the batch to the physically counted quantity."""

    batch_id: int
    counted_quantity: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class MedicineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    form: str
    reorder_level: int
    created_at: datetime


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    medicine_id: int
    batch_number: str
    expiry_date: date
    quantity: int
    created_at: datetime
    expired: bool  # computed at read time


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    medicine_id: int
    movement_type: str
    quantity: int
    balance_after: int
    note: str | None
    created_at: datetime


class StockReportRow(BaseModel):
    medicine_id: int
    name: str
    total_quantity: int
    batch_count: int
    expired_quantity: int
    expiring_soon_quantity: int  # expires within 90 days
    below_reorder_level: bool
    reorder_level: int


class ErrorBody(BaseModel):
    code: str
    message: str
