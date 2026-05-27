"""
Tests de endpoints de la API — usan TestClient de FastAPI con mocks.
No llaman a Odoo ni a Supabase real.
"""
import pytest
import json
import uuid
from unittest.mock import patch
from .conftest import SAMPLE_ORDER, SAMPLE_TRANSIT_DATA


# ── /api/transit ──────────────────────────────────────────────────────────────

class TestTransitGet:
    def test_devuelve_lista(self, client):
        with patch("app.read_transit", return_value=SAMPLE_TRANSIT_DATA):
            r = client.get("/api/transit")
        assert r.status_code == 200
        assert "orders" in r.json()

    def test_estructura_pedido(self, client):
        with patch("app.read_transit", return_value=SAMPLE_TRANSIT_DATA):
            r = client.get("/api/transit")
        order = r.json()["orders"][0]
        for campo in ["id", "ref", "status", "lines", "payments"]:
            assert campo in order, f"Falta campo '{campo}'"

    def test_sin_pedidos(self, client):
        with patch("app.read_transit", return_value={"orders": []}):
            r = client.get("/api/transit")
        assert r.status_code == 200
        assert r.json()["orders"] == []


class TestTransitCreate:
    def test_crea_pedido_basico(self, client):
        payload = {
            "ref": "TEST-NEW",
            "supplier": "Factory",
            "order_date": "2026-05-01",
            "expected_arrival": "2026-08-01",
            "currency": "USD",
            "lines": [
                {"product_id": 100, "product_name": "Producto", "qty": 5, "price": 300.0}
            ],
        }
        with patch("app.read_transit", return_value={"orders": []}), \
             patch("app.write_transit") as mock_write:
            r = client.post("/api/transit", json=payload)
        assert r.status_code == 200
        assert mock_write.called

    def test_pedido_recibe_id_unico(self, client):
        """Cada pedido nuevo debe tener un id generado."""
        payload = {"ref": "TEST-ID", "lines": []}
        with patch("app.read_transit", return_value={"orders": []}), \
             patch("app.write_transit") as mock_write:
            r = client.post("/api/transit", json=payload)
        assert r.status_code == 200
        written = mock_write.call_args[0][0]
        assert len(written["orders"]) == 1
        assert written["orders"][0]["id"]  # id no vacío


class TestTransitArchive:
    def test_archiva_pedido_existente(self, client):
        order_id = SAMPLE_ORDER["id"]
        data = {"orders": [dict(SAMPLE_ORDER)]}
        with patch("app.read_transit", return_value=data), \
             patch("app.write_transit") as mock_write:
            r = client.patch(
                f"/api/transit/{order_id}/archive",
                json={"actual_arrival": "2026-05-20"},
            )
        assert r.status_code == 200
        written = mock_write.call_args[0][0]
        order = next(o for o in written["orders"] if o["id"] == order_id)
        assert order["status"] == "archived"
        assert order["actual_arrival"] == "2026-05-20"

    def test_archiva_pedido_inexistente_no_falla(self, client):
        """Si el pedido no existe, archive devuelve 200 sin romper nada (silencioso)."""
        with patch("app.read_transit", return_value={"orders": []}), \
             patch("app.write_transit"):
            r = client.patch(
                "/api/transit/no-existe/archive",
                json={"actual_arrival": "2026-05-20"},
            )
        assert r.status_code == 200  # comportamiento actual: silencioso


class TestTransitUnarchive:
    def test_desarchiva_pedido(self, client):
        order = {**SAMPLE_ORDER, "status": "archived"}
        data = {"orders": [order]}
        with patch("app.read_transit", return_value=data), \
             patch("app.write_transit") as mock_write:
            r = client.patch(f"/api/transit/{order['id']}/unarchive")
        assert r.status_code == 200
        written = mock_write.call_args[0][0]
        restored = next(o for o in written["orders"] if o["id"] == order["id"])
        assert restored["status"] == "active"


# ── /api/transit/{id}/file (delete) ──────────────────────────────────────────

class TestDeleteFile:
    def _delete(self, client, order_id, path):
        """Helper: DELETE con body JSON."""
        return client.request(
            "DELETE",
            f"/api/transit/{order_id}/file",
            data=json.dumps({"path": path}),
            headers={"content-type": "application/json"},
        )

    def test_rechaza_path_de_otro_pedido(self, client):
        order_id = SAMPLE_ORDER["id"]
        r = self._delete(client, order_id, "otro-pedido-999/archivo.pdf")
        assert r.status_code == 403

    def test_rechaza_path_vacio(self, client):
        order_id = SAMPLE_ORDER["id"]
        r = self._delete(client, order_id, "")
        assert r.status_code == 400

    def test_rechaza_sin_supabase(self, client):
        """Sin Supabase configurado el endpoint devuelve 503."""
        order_id = SAMPLE_ORDER["id"]
        path = f"{order_id}/factura.pdf"
        r = self._delete(client, order_id, path)
        assert r.status_code == 503


# ── /api/transit/{id}/payment ─────────────────────────────────────────────────

class TestPayments:
    def test_guarda_pago(self, client):
        order_id = SAMPLE_ORDER["id"]
        data = {"orders": [dict(SAMPLE_ORDER)]}
        pago = {
            "payments": [
                {"id": str(uuid.uuid4()), "description": "Depósito",
                 "amount": "5000", "date": "2026-05-01"}
            ]
        }
        with patch("app.read_transit", return_value=data), \
             patch("app.write_transit") as mock_write:
            r = client.patch(f"/api/transit/{order_id}/payment", json=pago)
        assert r.status_code == 200
        written = mock_write.call_args[0][0]
        order = next(o for o in written["orders"] if o["id"] == order_id)
        assert len(order["payments"]) == 1
        assert order["payments"][0]["amount"] == "5000"

    def test_pago_pedido_inexistente(self, client):
        with patch("app.read_transit", return_value={"orders": []}):
            r = client.patch("/api/transit/no-existe/payment",
                           json={"payments": []})
        assert r.status_code == 404


# ── /api/forecast/refresh ─────────────────────────────────────────────────────

class TestForecastRefresh:
    def test_limpia_cache(self, client):
        r = client.get("/api/forecast/refresh")
        assert r.status_code == 200
        assert r.json().get("cleared") is True


# ── /api/exchange-rates ───────────────────────────────────────────────────────

class TestRates:
    def test_devuelve_estructura(self, client):
        """El endpoint de tipos de cambio devuelve un dict con claves de moneda."""
        mock_rates = {"USD": 1.08, "GBP": 0.85}
        with patch("app.cache_get", return_value=mock_rates):
            r = client.get("/api/exchange-rates")
        assert r.status_code == 200
        assert "USD" in r.json()

    def test_usd_es_positivo(self, client):
        mock_rates = {"USD": 1.08}
        with patch("app.cache_get", return_value=mock_rates):
            r = client.get("/api/exchange-rates")
        assert r.json()["USD"] > 0
