"""
Configuración de tests — mocks para Odoo y Supabase.
Los tests corren sin conexión real a ningún servicio externo.
"""
import os
import pytest
from fastapi.testclient import TestClient

# Variables dummy ANTES de importar app.
# SUPABASE_URL/KEY vacíos → _supabase = None → la app usa transit_orders.json (mockeamos read/write_transit)
os.environ["ODOO_URL"]      = "http://fake-odoo.test"
os.environ["ODOO_DB"]       = "fake_db"
os.environ["ODOO_USERNAME"] = "test@test.com"
os.environ["ODOO_PASSWORD"] = "fake_password"
os.environ["SUPABASE_URL"]  = ""   # vacío → _supabase queda None
os.environ["SUPABASE_KEY"]  = ""


@pytest.fixture(scope="session")
def client():
    """TestClient de FastAPI listo para usar en todos los tests."""
    from app import app as fastapi_app
    return TestClient(fastapi_app, raise_server_exceptions=False)


# ── Datos de ejemplo reutilizables ────────────────────────────────────────────

SAMPLE_ORDER = {
    "id": "test-order-001",
    "ref": "TEST-1",
    "supplier": "Factory Test",
    "order_date": "2026-01-15",
    "expected_arrival": "2026-06-01",
    "status": "active",
    "currency": "USD",
    "lines": [
        {"id": "line-1", "product_id": 100, "product_name": "Producto A", "qty": 10, "price": 500.0},
        {"id": "line-2", "product_id": 101, "product_name": "Producto B", "qty": 5,  "price": 200.0},
    ],
    "payments": [],
    "files": [],
    "created_at": "2026-01-10T10:00:00",
}

SAMPLE_TRANSIT_DATA = {"orders": [SAMPLE_ORDER]}
