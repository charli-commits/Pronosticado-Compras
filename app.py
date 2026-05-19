import os
import time
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

ODOO_URL      = os.getenv("ODOO_URL", "")
ODOO_DB       = os.getenv("ODOO_DB", "")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
ODOO_HTTP_USER = os.getenv("ODOO_HTTP_USER", "")
ODOO_HTTP_PASS = os.getenv("ODOO_HTTP_PASS", "")

# Brands to include in forecast (Titanium Strength, Force USA, Nordictrack)
TARGET_BRAND_IDS = [52, 39, 45]
TARGET_BRANDS    = [
    {"id": 52, "name": "Titanium Strength"},
    {"id": 39, "name": "Force USA"},
    {"id": 45, "name": "Nordictrack"},
]

# Stock locations to monitor (ESBO/Stock, ESBO/Expo Caja)
ALLOWED_LOCATION_IDS = [12, 142]
LOCATIONS = [
    {"id": 12,  "name": "ESBO/Stock"},
    {"id": 142, "name": "ESBO/Expo Caja"},
]

FORECAST_LEAD_MONTHS   = 6    # container lead time
FORECAST_SMOOTHING_ALPHA = 0.3  # exponential smoothing factor
CACHE_TTL_SECONDS      = 300  # 5 minutes

# ── Cache ─────────────────────────────────────────────────────────────────────

_cache: dict = {}


def cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
        return entry["data"]
    return None


def cache_set(key: str, data: dict) -> None:
    _cache[key] = {"data": data, "ts": time.time()}


def cache_clear(key: str) -> None:
    _cache.pop(key, None)


# ── Odoo JSON-RPC client ──────────────────────────────────────────────────────

_session_uid: Optional[int] = None


def _rpc(service: str, method: str, args: list) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "method":  "call",
        "id":      1,
        "params":  {"service": service, "method": method, "args": args},
    }
    http_auth = (ODOO_HTTP_USER, ODOO_HTTP_PASS) if ODOO_HTTP_USER else None
    try:
        response = requests.post(
            f"{ODOO_URL}/jsonrpc",
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
            auth=http_auth,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            message = body["error"].get("data", {}).get("message", str(body["error"]))
            raise HTTPException(status_code=400, detail=message)
        return body.get("result")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail=f"No se puede conectar a Odoo en {ODOO_URL}")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Odoo no respondió a tiempo")


def _get_uid() -> int:
    global _session_uid
    if not _session_uid:
        uid = _rpc("common", "login", [ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD])
        if not uid:
            raise HTTPException(status_code=401, detail="Credenciales de Odoo inválidas")
        _session_uid = uid
    return _session_uid


def odoo(model: str, method: str, domain: list = None, fields: list = None, **kwargs) -> list:
    """Shorthand for execute_kw. Domain maps to args, the rest go to kwargs."""
    uid = _get_uid()
    rpc_kwargs = {"fields": fields, **kwargs} if fields else kwargs
    return _rpc("object", "execute_kw", [
        ODOO_DB, uid, ODOO_PASSWORD,
        model, method,
        [domain or []],
        rpc_kwargs,
    ])


# ── Forecast helpers ──────────────────────────────────────────────────────────

def build_months_timeline(from_date: datetime, n_months: int = 12) -> list[str]:
    """Returns a list of YYYY-MM strings from oldest to most recent."""
    return [
        (from_date - timedelta(days=i * 30)).strftime("%Y-%m")
        for i in range(n_months - 1, -1, -1)
    ]


def exponential_smooth(series: list[float], alpha: float = FORECAST_SMOOTHING_ALPHA) -> list[float]:
    smoothed = [series[0]]
    for value in series[1:]:
        smoothed.append(alpha * value + (1 - alpha) * smoothed[-1])
    return smoothed


def linear_slope(values: list[float]) -> float:
    """Least-squares slope over an evenly-spaced series."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    numerator   = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator if denominator else 0.0


def classify_trend(slope: float) -> str:
    if slope > 0.5:
        return "up"
    if slope < -0.5:
        return "down"
    return "stable"


def project_forecast(base: float, slope: float, from_date: datetime, months: int) -> list[dict]:
    return [
        {
            "month": (from_date + timedelta(days=i * 30)).strftime("%b %Y"),
            "qty":   round(max(0.0, base + slope * i), 2),
        }
        for i in range(1, months + 1)
    ]


def suggested_order_qty(forecast_total: float, monthly_velocity: float,
                        virtual_stock: float, lead_months: int) -> int:
    """Units to order = max(forecast, velocity floor) minus what we already have."""
    demand = max(forecast_total, monthly_velocity * lead_months)
    return int(max(0.0, round(demand - virtual_stock, 0)))


# ── Odoo data fetchers ────────────────────────────────────────────────────────

def fetch_product_variants(brand_ids: list[int]) -> dict[int, dict]:
    """Returns a map of product_id → brand/sku/name info."""
    rows = odoo(
        "product.product", "search_read",
        domain=[["product_brand_id", "in", brand_ids]],
        fields=["id", "product_tmpl_id", "product_brand_id", "old_sku", "display_name"],
        limit=5000,
    )
    return {
        row["id"]: {
            "brand_id":   row["product_brand_id"][0] if row.get("product_brand_id") else None,
            "brand_name": row["product_brand_id"][1] if row.get("product_brand_id") else "Sin marca",
            "old_sku":    row.get("old_sku") or "",
            "name":       row.get("display_name") or "",
            "tmpl_id":    row["product_tmpl_id"][0] if row.get("product_tmpl_id") else None,
        }
        for row in rows
    }


def fetch_sales_by_month(product_ids: list[int], since: str) -> dict[int, dict[str, float]]:
    """Returns sales quantities grouped by product_id → month key (YYYY-MM)."""
    lines = odoo(
        "sale.order.line", "search_read",
        domain=[
            ["product_id", "in", product_ids],
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", since],
        ],
        fields=["product_id", "product_uom_qty", "order_id"],
        limit=50000,
    )
    order_ids = list({line["order_id"][0] for line in lines if line.get("order_id")})
    orders = odoo(
        "sale.order", "search_read",
        domain=[["id", "in", order_ids]],
        fields=["id", "date_order"],
        limit=len(order_ids) + 1,
    )
    order_month = {o["id"]: o["date_order"][:7] for o in orders if o.get("date_order")}

    sales: dict = defaultdict(lambda: defaultdict(float))
    for line in lines:
        if not line.get("product_id") or not line.get("order_id"):
            continue
        product_id = line["product_id"][0]
        month_key  = order_month.get(line["order_id"][0], "")
        if month_key:
            sales[product_id][month_key] += line.get("product_uom_qty") or 0
    return sales


def fetch_po_vendor_and_price(product_ids: list[int], since: str) -> tuple[dict, dict]:
    """Returns (vendor_votes_by_product, price_data_by_product) from PO history."""
    lines = odoo(
        "purchase.order.line", "search_read",
        domain=[
            ["order_id.state", "in", ["purchase", "done"]],
            ["order_id.date_approve", ">=", since],
            ["product_id", "in", product_ids],
        ],
        fields=["product_id", "product_qty", "price_subtotal", "order_id"],
        limit=10000,
    )
    order_ids = list({line["order_id"][0] for line in lines if line.get("order_id")})
    orders = odoo(
        "purchase.order", "search_read",
        domain=[["id", "in", order_ids]],
        fields=["id", "partner_id"],
        limit=len(order_ids) + 1,
    )
    vendor_by_order = {o["id"]: o["partner_id"][1] for o in orders if o.get("partner_id")}

    vendor_votes: dict = defaultdict(lambda: defaultdict(int))
    price_data:   dict = defaultdict(lambda: {"qty": 0.0, "amount": 0.0})

    for line in lines:
        if not line.get("product_id"):
            continue
        product_id = line["product_id"][0]
        order_id   = line["order_id"][0]
        vendor     = vendor_by_order.get(order_id)
        if vendor:
            vendor_votes[product_id][vendor] += 1
        price_data[product_id]["qty"]    += line.get("product_qty") or 0
        price_data[product_id]["amount"] += line.get("price_subtotal") or 0

    return vendor_votes, price_data


def fetch_stock(product_ids: list[int]) -> dict[int, dict]:
    rows = odoo(
        "product.product", "search_read",
        domain=[["id", "in", product_ids]],
        fields=["id", "qty_available", "virtual_available", "incoming_qty", "outgoing_qty"],
        limit=len(product_ids) + 1,
    )
    return {row["id"]: row for row in rows}


def fetch_supplier_info(template_ids: list[int]) -> dict[int, dict]:
    """Returns primary supplier code + price keyed by product_tmpl_id."""
    rows = odoo(
        "product.supplierinfo", "search_read",
        domain=[["product_tmpl_id", "in", template_ids]],
        fields=["product_tmpl_id", "partner_id", "price", "product_code", "sequence"],
        order="sequence asc",
        limit=len(template_ids) * 3,
    )
    result = {}
    for row in rows:
        tmpl_id = row["product_tmpl_id"][0]
        if tmpl_id not in result:
            result[tmpl_id] = {
                "vendor_code":  row.get("product_code") or "",
                "vendor_price": round(row.get("price") or 0, 4),
            }
    return result


def fetch_sales_by_product_12m(since: str) -> dict[int, float]:
    """Used by stock alerts: total units sold per product in last 12 months."""
    rows = odoo(
        "sale.order.line", "read_group",
        domain=[
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", since],
        ],
        fields=["product_id", "product_uom_qty:sum"],
        groupby=["product_id"],
        limit=5000,
    )
    return {
        row["product_id"][0]: round(row.get("product_uom_qty") or 0, 2)
        for row in rows
        if row.get("product_id")
    }


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Odoo Purchasing Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    if not ODOO_URL or not ODOO_DB:
        return {"connected": False, "message": "Faltan variables de entorno (.env)"}
    try:
        uid = _get_uid()
        return {"connected": True, "uid": uid, "url": ODOO_URL, "db": ODOO_DB}
    except HTTPException as e:
        return {"connected": False, "message": e.detail}


@app.get("/api/dashboard")
def dashboard():
    now         = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start  = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    confirmed   = ["purchase", "done"]

    def po_amount(extra_domain: list) -> float:
        res = odoo("purchase.order", "read_group",
                   domain=extra_domain,
                   fields=["amount_total:sum"],
                   groupby=[])
        return (res[0]["amount_total"] or 0) if res else 0

    def po_count(domain: list) -> int:
        return odoo("purchase.order", "search_count", domain=domain)

    def monthly_trend() -> list[dict]:
        trend = []
        for i in range(11, -1, -1):
            month_start_d = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
            month_end_d   = (
                month_start_d.replace(month=month_start_d.month % 12 + 1, day=1)
                if month_start_d.month < 12
                else month_start_d.replace(year=month_start_d.year + 1, month=1, day=1)
            )
            res = odoo("purchase.order", "read_group",
                       domain=[
                           ["state", "in", confirmed],
                           ["date_approve", ">=", month_start_d.strftime("%Y-%m-%d")],
                           ["date_approve", "<",  month_end_d.strftime("%Y-%m-%d")],
                       ],
                       fields=["amount_total:sum"],
                       groupby=[])
            trend.append({
                "month":  month_start_d.strftime("%b %Y"),
                "amount": round((res[0]["amount_total"] or 0) if res else 0, 2),
            })
        return trend

    top_suppliers_raw = odoo(
        "purchase.order", "read_group",
        domain=[["state", "in", confirmed], ["date_approve", ">=", year_start.strftime("%Y-%m-%d")]],
        fields=["partner_id", "amount_total:sum"],
        groupby=["partner_id"],
        limit=10,
        orderby="amount_total desc",
    )

    last_pos = odoo(
        "purchase.order", "search_read",
        domain=[],
        fields=["name", "partner_id", "date_order", "amount_total", "state", "currency_id"],
        order="date_order desc",
        limit=20,
    )

    return {
        "kpis": {
            "spend_month":     round(po_amount([["state", "in", confirmed], ["date_approve", ">=", month_start.strftime("%Y-%m-%d %H:%M:%S")]]), 2),
            "spend_ytd":       round(po_amount([["state", "in", confirmed], ["date_approve", ">=", year_start.strftime("%Y-%m-%d %H:%M:%S")]]), 2),
            "orders_month":    po_count([["date_order", ">=", month_start.strftime("%Y-%m-%d %H:%M:%S")]]),
            "draft_count":     po_count([["state", "=", "draft"]]),
            "confirmed_count": po_count([["state", "in", confirmed]]),
            "low_stock_count": odoo("stock.warehouse.orderpoint", "search_count",
                                    domain=[["qty_on_hand", "<", "product_min_qty"]]),
        },
        "trend": monthly_trend(),
        "top_suppliers": [
            {"name": r["partner_id"][1], "amount": round(r["amount_total"] or 0, 2)}
            for r in (top_suppliers_raw or [])
        ],
        "last_orders": [
            {
                "id":       r["id"],
                "name":     r["name"],
                "supplier": r["partner_id"][1] if r.get("partner_id") else "—",
                "date":     (r.get("date_order") or "")[:10],
                "amount":   round(r.get("amount_total") or 0, 2),
                "currency": r["currency_id"][1] if r.get("currency_id") else "EUR",
                "state":    r.get("state", ""),
            }
            for r in (last_pos or [])
        ],
    }


@app.get("/api/stock-alerts")
def stock_alerts():
    since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    orderpoints = odoo(
        "stock.warehouse.orderpoint", "search_read",
        domain=[["location_id", "in", ALLOWED_LOCATION_IDS]],
        fields=["product_id", "location_id", "qty_on_hand", "product_min_qty", "product_max_qty", "qty_to_order"],
        limit=2000,
    )

    sales_12m = fetch_sales_by_product_12m(since)

    def severity(on_hand: float, min_qty: float) -> str:
        if on_hand <= 0:              return "critical"
        if on_hand < min_qty:         return "warning"
        if on_hand < min_qty * 1.2:   return "info"
        return "ok"

    SEV_ORDER = ["critical", "warning", "info", "ok"]

    alerts = [
        {
            "id":          op["id"],
            "product":     op["product_id"][1] if op.get("product_id") else "—",
            "location_id": op["location_id"][0] if op.get("location_id") else None,
            "location":    op["location_id"][1] if op.get("location_id") else "—",
            "on_hand":     round(op.get("qty_on_hand") or 0, 2),
            "min_qty":     round(op.get("product_min_qty") or 0, 2),
            "max_qty":     round(op.get("product_max_qty") or 0, 2),
            "to_order":    round(op.get("qty_to_order") or 0, 2),
            "severity":    severity(op.get("qty_on_hand") or 0, op.get("product_min_qty") or 0),
            "coverage":    min(round(((op.get("qty_on_hand") or 0) / (op.get("product_min_qty") or 1)) * 100, 1), 200),
            "sales_12m":   sales_12m.get(op["product_id"][0] if op.get("product_id") else 0, 0),
        }
        for op in (orderpoints or [])
    ]

    alerts.sort(key=lambda x: (SEV_ORDER.index(x["severity"]), -x["sales_12m"]))

    return {"alerts": alerts, "locations": LOCATIONS}


@app.get("/api/forecast/refresh")
def forecast_refresh():
    cache_clear("forecast")
    return {"cleared": True}


@app.get("/api/forecast")
def forecast():
    cached = cache_get("forecast")
    if cached:
        return cached

    now   = datetime.now()
    since = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    timeline = build_months_timeline(now, n_months=12)

    # Fetch all raw data from Odoo
    product_info  = fetch_product_variants(TARGET_BRAND_IDS)
    if not product_info:
        return {"products": [], "brands": TARGET_BRANDS}

    product_ids   = list(product_info.keys())
    sales_history = fetch_sales_by_month(product_ids, since)
    vendor_votes, price_data = fetch_po_vendor_and_price(product_ids, since)

    def dominant_vendor(product_id: int) -> str:
        votes = vendor_votes.get(product_id, {})
        return max(votes, key=votes.__getitem__) if votes else "—"

    def avg_purchase_price(product_id: int) -> float:
        data = price_data.get(product_id, {})
        total_qty = data.get("qty", 0)
        return round(data.get("amount", 0) / total_qty, 4) if total_qty > 0 else 0.0

    # Build forecast for each product
    results = []
    for product_id in product_ids:
        monthly_sales = [sales_history[product_id].get(m, 0.0) for m in timeline]
        info = product_info[product_id]

        base_record = {
            "product_id":   product_id,
            "product_name": info["name"],
            "old_sku":      info["old_sku"],
            "brand_id":     info["brand_id"],
            "brand_name":   info["brand_name"],
            "avg_price":    avg_purchase_price(product_id),
        }

        if sum(monthly_sales) == 0:
            results.append({
                **base_record,
                "history":            [{"month": m, "qty": 0.0} for m in timeline],
                "forecast":           [],
                "trend":              "stable",
                "monthly_velocity":   0.0,
                "total_forecast_qty": 0.0,
                "no_movement":        True,
                "no_recent_sales":    True,
            })
            continue

        no_recent_sales = all(monthly_sales[i] == 0 for i in [-1, -2, -3])
        smoothed        = exponential_smooth(monthly_sales)
        slope           = linear_slope(smoothed[-6:])
        monthly_velocity = sum(smoothed[-3:]) / 3
        forecast_months  = project_forecast(smoothed[-1], slope, now, FORECAST_LEAD_MONTHS)
        total_forecast   = sum(f["qty"] for f in forecast_months)

        results.append({
            **base_record,
            "history":            [{"month": m, "qty": round(monthly_sales[i], 2)} for i, m in enumerate(timeline)],
            "forecast":           forecast_months,
            "trend":              classify_trend(slope),
            "monthly_velocity":   round(monthly_velocity, 2),
            "total_forecast_qty": round(total_forecast, 2),
            "no_movement":        False,
            "no_recent_sales":    no_recent_sales,
        })

    results.sort(key=lambda x: x["total_forecast_qty"], reverse=True)

    # Enrich with live stock + supplier info
    stock_map    = fetch_stock([r["product_id"] for r in results])
    tmpl_by_pid  = {pid: info["tmpl_id"] for pid, info in product_info.items() if info.get("tmpl_id")}
    supplier_map = fetch_supplier_info(list({t for t in tmpl_by_pid.values() if t}))

    for record in results:
        product_id   = record["product_id"]
        stock        = stock_map.get(product_id, {})
        virtual_stock = round(stock.get("virtual_available") or 0, 2)

        record["qty_available"]     = round(stock.get("qty_available")  or 0, 2)
        record["virtual_available"] = virtual_stock
        record["incoming_qty"]      = round(stock.get("incoming_qty")   or 0, 2)
        record["outgoing_qty"]      = round(stock.get("outgoing_qty")   or 0, 2)

        velocity = record["monthly_velocity"]
        record["coverage_months"] = round(virtual_stock / velocity, 1) if velocity > 0 else 99.0
        record["suggested_qty"]   = suggested_order_qty(
            record["total_forecast_qty"], velocity, virtual_stock, FORECAST_LEAD_MONTHS
        )
        record["vendor"]       = dominant_vendor(product_id)

        tmpl_id       = tmpl_by_pid.get(product_id)
        supplier_info = supplier_map.get(tmpl_id, {}) if tmpl_id else {}
        record["vendor_code"]  = supplier_info.get("vendor_code", "")
        record["vendor_price"] = supplier_info.get("vendor_price") or record["avg_price"]

    result = {"products": results, "brands": TARGET_BRANDS}
    cache_set("forecast", result)
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
