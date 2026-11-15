"""HTTP-level tests: endpoints, validation and the report."""

from datetime import date, timedelta


def test_health_reports_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_ready_with_database_up(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "up"}


def test_every_response_carries_request_id(client):
    response = client.get("/health")
    assert response.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# Validation (Pydantic layer)
# ---------------------------------------------------------------------------


def test_negative_quantity_rejected(client):
    response = client.post("/sales", json={"batch_id": 1, "quantity": -5})
    assert response.status_code == 422
    assert "greater than 0" in response.text


def test_zero_quantity_rejected(client):
    response = client.post("/purchases", json={"batch_id": 1, "quantity": 0})
    assert response.status_code == 422


def test_batch_requires_batch_number_and_expiry(client):
    response = client.post(
        "/batches",
        json={"medicine_id": 1, "quantity": 10},  # missing batch_number + expiry_date
    )
    assert response.status_code == 422


def test_negative_opening_stock_rejected(client):
    response = client.post(
        "/batches",
        json={
            "medicine_id": 1,
            "batch_number": "B",
            "expiry_date": "2027-01-01",
            "quantity": -10,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Workflow through the public API
# ---------------------------------------------------------------------------


def _create_medicine(client) -> int:
    response = client.post(
        "/medicines", json={"name": "Paracetamol 500mg", "form": "tablet", "reorder_level": 10}
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_batch(client, medicine_id, number, days, quantity) -> dict:
    response = client.post(
        "/batches",
        json={
            "medicine_id": medicine_id,
            "batch_number": number,
            "expiry_date": str(date.today() + timedelta(days=days)),
            "quantity": quantity,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_full_stock_workflow(client):
    medicine_id = _create_medicine(client)
    batch = _create_batch(client, medicine_id, "PARA-2027", days=365, quantity=50)

    # purchase, sale, return, adjustment — all through the API
    assert (
        client.post("/purchases", json={"batch_id": batch["id"], "quantity": 10}).status_code == 201
    )
    assert client.post("/sales", json={"batch_id": batch["id"], "quantity": 5}).status_code == 201
    assert client.post("/returns", json={"batch_id": batch["id"], "quantity": 1}).status_code == 201
    assert (
        client.post(
            "/adjustments", json={"batch_id": batch["id"], "counted_quantity": 55}
        ).status_code
        == 201
    )  # 50+10-5+1 = 56, counted 55

    # current batch state
    batches = client.get("/batches", params={"medicine_id": medicine_id}).json()
    assert len(batches) == 1
    assert batches[0]["quantity"] == 55
    assert batches[0]["expired"] is False

    # audit trail: opening stock + 4 operations, newest first
    movements = client.get("/movements", params={"batch_id": batch["id"]}).json()
    assert [m["movement_type"] for m in movements] == [
        "ADJUSTMENT",
        "RETURN",
        "SALE",
        "PURCHASE",
        "PURCHASE",
    ]
    assert movements[0]["balance_after"] == 55

    # report reflects the same position
    report = client.get("/report/stock").json()
    row = next(r for r in report if r["medicine_id"] == medicine_id)
    assert row["total_quantity"] == 55
    assert row["below_reorder_level"] is False


def test_expired_batch_sale_returns_machine_readable_error(client):
    medicine_id = _create_medicine(client)
    batch = _create_batch(client, medicine_id, "EXP-OLD", days=-10, quantity=20)

    response = client.post("/sales", json={"batch_id": batch["id"], "quantity": 5})
    assert response.status_code == 422
    assert response.json()["code"] == "EXPIRED_BATCH"

    # stock unchanged, no sale recorded
    assert client.get("/batches").json()[0]["quantity"] == 20
    assert (
        client.get("/movements", params={"batch_id": batch["id"]}).json()[0]["movement_type"]
        == "PURCHASE"
    )


def test_report_flags_expired_and_low_stock(client):
    medicine_id = _create_medicine(client)
    _create_batch(client, medicine_id, "EXP", days=-1, quantity=30)  # expired stock
    _create_batch(client, medicine_id, "LOW", days=60, quantity=2)  # below reorder_level 10

    row = next(r for r in client.get("/report/stock").json() if r["medicine_id"] == medicine_id)
    assert row["expired_quantity"] == 30
    assert row["expiring_soon_quantity"] == 2
    assert row["total_quantity"] == 32
    assert row["below_reorder_level"] is False  # 32 >= reorder level 10


def test_unknown_medicine_returns_404(client):
    response = client.post(
        "/batches",
        json={
            "medicine_id": 999,
            "batch_number": "X",
            "expiry_date": "2027-01-01",
            "quantity": 1,
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "MEDICINE_NOT_FOUND"
