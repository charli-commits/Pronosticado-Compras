"""
Tests de lógica pura — no necesitan conexión a Odoo ni Supabase.
Cubren las funciones de cálculo que si fallan dan recomendaciones incorrectas.
"""
import pytest
from datetime import datetime
# conftest.py ya pone SUPABASE_URL="" antes de este import
from app import suggested_order_qty, exponential_smooth, classify_trend, project_forecast


# ── suggested_order_qty ───────────────────────────────────────────────────────

class TestSuggestedOrderQty:
    def test_basic_compra(self):
        """Si el forecast supera el stock, se sugiere comprar la diferencia."""
        qty = suggested_order_qty(
            forecast_total=100,
            monthly_velocity=20,
            virtual_stock=30,
            lead_months=5,
        )
        assert qty == 70  # 100 - 30

    def test_sin_necesidad(self):
        """Si hay más stock que forecast, no se sugiere comprar nada."""
        qty = suggested_order_qty(
            forecast_total=50,
            monthly_velocity=10,
            virtual_stock=200,
            lead_months=5,
        )
        assert qty == 0

    def test_velocity_mayor_que_forecast(self):
        """La demanda calculada es max(forecast, velocity × lead_months)."""
        qty = suggested_order_qty(
            forecast_total=10,
            monthly_velocity=30,  # 30 × 5 = 150 → mayor que forecast
            virtual_stock=20,
            lead_months=5,
        )
        assert qty == 130  # 150 - 20

    def test_stock_negativo(self):
        """Stock negativo (rotura) aumenta la cantidad sugerida."""
        qty = suggested_order_qty(
            forecast_total=100,
            monthly_velocity=20,
            virtual_stock=-10,
            lead_months=5,
        )
        assert qty == 110  # 100 - (-10)

    def test_resultado_nunca_negativo(self):
        """La cantidad sugerida nunca es negativa."""
        qty = suggested_order_qty(
            forecast_total=0,
            monthly_velocity=0,
            virtual_stock=1000,
            lead_months=5,
        )
        assert qty == 0


# ── exponential_smooth ────────────────────────────────────────────────────────

class TestExponentialSmooth:
    def test_serie_estable(self):
        """Serie constante → el suavizado no cambia los valores."""
        series = [10.0] * 6
        result = exponential_smooth(series)
        assert len(result) == 6
        assert all(abs(v - 10.0) < 0.01 for v in result)

    def test_pico_se_suaviza(self):
        """Un pico puntual se atenúa en la serie suavizada."""
        series = [10.0, 10.0, 10.0, 100.0, 10.0, 10.0]
        result = exponential_smooth(series)
        assert result[3] < 100.0   # el pico baja
        assert result[4] > 10.0    # el efecto se arrastra un poco

    def test_longitud_preservada(self):
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = exponential_smooth(series)
        assert len(result) == len(series)


# ── classify_trend ────────────────────────────────────────────────────────────

class TestClassifyTrend:
    def test_creciente(self):
        assert classify_trend(5.0) == "up"

    def test_decreciente(self):
        assert classify_trend(-5.0) == "down"

    def test_estable(self):
        assert classify_trend(0.0) == "stable"
        assert classify_trend(0.5) == "stable"
        assert classify_trend(-0.5) == "stable"


# ── project_forecast ──────────────────────────────────────────────────────────

class TestProjectForecast:
    def test_genera_meses_correctos(self):
        from_date = datetime(2026, 5, 1)
        result = project_forecast(base=10.0, slope=0.0, from_date=from_date, months=5)
        assert len(result) == 5
        assert all("month" in r and "qty" in r for r in result)

    def test_cantidad_nunca_negativa(self):
        """Aunque la tendencia sea muy negativa, qty >= 0."""
        from_date = datetime(2026, 5, 1)
        result = project_forecast(base=5.0, slope=-100.0, from_date=from_date, months=5)
        assert all(r["qty"] >= 0 for r in result)

    def test_tendencia_creciente(self):
        """Con slope positivo, las cantidades deben crecer o mantenerse."""
        from_date = datetime(2026, 5, 1)
        result = project_forecast(base=10.0, slope=2.0, from_date=from_date, months=5)
        qtys = [r["qty"] for r in result]
        assert qtys[-1] >= qtys[0]


# ── SKU prefix exclusion ──────────────────────────────────────────────────────

class TestSkuPrefixExclusion:
    """Verifica que la lógica de exclusión por prefijo SKU funciona correctamente."""

    SKU_COMPONENT_PREFIXES = ("R200-", "MG3L-", "TS-MX90-", "FT90-")

    def _should_exclude(self, sku: str) -> bool:
        return any(sku.upper().startswith(p) for p in self.SKU_COMPONENT_PREFIXES)

    def test_excluye_r200(self):
        assert self._should_exclude("R200-5") is True
        assert self._should_exclude("R200-1") is True

    def test_excluye_mx90(self):
        assert self._should_exclude("TS-MX90-2") is True
        assert self._should_exclude("TS-MX90-9") is True

    def test_no_excluye_producto_normal(self):
        assert self._should_exclude("TR60") is False
        assert self._should_exclude("B200V4") is False
        assert self._should_exclude("ELRX-S60B") is False

    def test_no_excluye_padre_r200(self):
        """El producto padre R200 no debe excluirse, solo R200-N."""
        assert self._should_exclude("R200") is False


# ── Path security (file delete) ───────────────────────────────────────────────

class TestPathSecurity:
    """Verifica la lógica de validación del path al borrar archivos."""

    def _is_valid_path(self, order_id: str, path: str) -> bool:
        return path.startswith(f"{order_id}/")

    def test_path_valido(self):
        assert self._is_valid_path("order-123", "order-123/factura.pdf") is True

    def test_path_de_otro_pedido(self):
        assert self._is_valid_path("order-123", "order-456/factura.pdf") is False

    def test_path_vacio(self):
        assert self._is_valid_path("order-123", "") is False

    def test_path_traversal(self):
        assert self._is_valid_path("order-123", "../order-123/factura.pdf") is False
