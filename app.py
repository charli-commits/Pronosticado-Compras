import json
import os
import time
import uuid
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from supabase import create_client, Client

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

# Primary stock location for forecast calculations (ESBO/Stock only)
STOCK_LOCATION_ID = 12

# Local file for transit orders (pedidos realizados pero no en Odoo aún)
TRANSIT_FILE = os.path.join(BASE_DIR, "transit_orders.json")

FORECAST_LEAD_MONTHS   = 5    # container lead time
FORECAST_SMOOTHING_ALPHA = 0.3  # exponential smoothing factor
CACHE_TTL_SECONDS      = 300  # 5 minutes

# Read-only share link token (set VIEW_TOKEN in .env to enable)
VIEW_TOKEN = os.getenv("VIEW_TOKEN", "")

# Supabase client for transit orders persistence
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

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
    """Returns on-hand, incoming, outgoing and virtual stock filtered to STOCK_LOCATION_ID only."""
    move_states = ["confirmed", "assigned", "waiting", "partially_available"]
    chunk = len(product_ids) + 1

    # On-hand quantity at ESBO/Stock (stock.quant)
    quant_rows = odoo(
        "stock.quant", "read_group",
        domain=[["location_id", "=", STOCK_LOCATION_ID], ["product_id", "in", product_ids]],
        fields=["product_id", "quantity:sum"],
        groupby=["product_id"],
        limit=chunk,
    )
    on_hand: dict[int, float] = {
        row["product_id"][0]: row.get("quantity") or 0.0
        for row in quant_rows if row.get("product_id")
    }

    # Incoming moves arriving TO ESBO/Stock
    incoming_rows = odoo(
        "stock.move", "read_group",
        domain=[
            ["location_dest_id", "=", STOCK_LOCATION_ID],
            ["state", "in", move_states],
            ["product_id", "in", product_ids],
        ],
        fields=["product_id", "product_qty:sum"],
        groupby=["product_id"],
        limit=chunk,
    )
    incoming: dict[int, float] = {
        row["product_id"][0]: row.get("product_qty") or 0.0
        for row in incoming_rows if row.get("product_id")
    }

    # Outgoing moves leaving FROM ESBO/Stock
    outgoing_rows = odoo(
        "stock.move", "read_group",
        domain=[
            ["location_id", "=", STOCK_LOCATION_ID],
            ["state", "in", move_states],
            ["product_id", "in", product_ids],
        ],
        fields=["product_id", "product_qty:sum"],
        groupby=["product_id"],
        limit=chunk,
    )
    outgoing: dict[int, float] = {
        row["product_id"][0]: row.get("product_qty") or 0.0
        for row in outgoing_rows if row.get("product_id")
    }

    return {
        pid: {
            "qty_available":     on_hand.get(pid, 0.0),
            "incoming_qty":      incoming.get(pid, 0.0),
            "outgoing_qty":      outgoing.get(pid, 0.0),
            "virtual_available": on_hand.get(pid, 0.0) + incoming.get(pid, 0.0) - outgoing.get(pid, 0.0),
        }
        for pid in product_ids
    }


def fetch_bom_component_ids(product_ids: list[int]) -> set[int]:
    """
    Returns product_ids to exclude from forecast.
    Rules:
    - Exclude products that are BoM components of a non-PACK parent
    - Never exclude a product that is itself a BoM parent (purchasable kit)
    - Never exclude components of PACK BoMs (those are individual purchasable products)
    """
    try:
        # 1. Find all BoM lines where our products appear as components
        all_lines = odoo(
            "mrp.bom.line", "search_read",
            domain=[["product_id", "in", product_ids]],
            fields=["product_id", "bom_id"],
            limit=len(product_ids) * 5,
        )
        if not all_lines:
            return set()

        all_bom_ids = list({l["bom_id"][0] for l in all_lines if l.get("bom_id")})

        # 2. Find which BoMs have a "separable" parent — components stay visible.
        #    PACK = bundles sold individually; DISC = disc sets (RP60, RP80… RP500, RP1000)
        #    "DISC" also matches "DISCOS" and "DISCS" via ilike substring.
        #    Odoo OR domain (Polish notation): [id_filter, "|", cond1, cond2]
        separable_boms = odoo(
            "mrp.bom", "search_read",
            domain=[
                ["id", "in", all_bom_ids],
                "|",
                ["product_tmpl_id.name", "ilike", "PACK"],
                ["product_tmpl_id.name", "ilike", "DISC"],
            ],
            fields=["id"],
            limit=len(all_bom_ids) + 1,
        )
        pack_bom_ids = {b["id"] for b in separable_boms}

        # 3. Candidates to exclude: components of non-separable BoMs
        candidates = {
            line["product_id"][0]
            for line in all_lines
            if line.get("product_id") and line["bom_id"][0] not in pack_bom_ids
        }

        # 4. Never exclude a product that is itself a BoM parent (it's a purchasable kit)
        if candidates:
            tmpl_ids_of_candidates = odoo(
                "product.product", "search_read",
                domain=[["id", "in", list(candidates)]],
                fields=["id", "product_tmpl_id"],
                limit=len(candidates) + 1,
            )
            tmpl_map = {r["id"]: r["product_tmpl_id"][0] for r in tmpl_ids_of_candidates if r.get("product_tmpl_id")}
            parent_boms = odoo(
                "mrp.bom", "search_read",
                domain=[["product_tmpl_id", "in", list(tmpl_map.values())]],
                fields=["product_tmpl_id"],
                limit=len(tmpl_map) + 1,
            )
            bom_parent_tmpl_ids = {b["product_tmpl_id"][0] for b in parent_boms}
            # Remove from candidates any product whose template has a BoM
            candidates = {
                pid for pid in candidates
                if tmpl_map.get(pid) not in bom_parent_tmpl_ids
            }

        return candidates
    except Exception:
        return set()


def fetch_kit_parent_ids(product_ids: list[int], tmpl_by_pid: dict[int, int]) -> set[int]:
    """Returns the subset of product_ids that are kit parents (have a BoM in Odoo)."""
    try:
        tmpl_ids = [tmpl_by_pid[pid] for pid in product_ids if tmpl_by_pid.get(pid)]
        if not tmpl_ids:
            return set()
        boms = odoo(
            "mrp.bom", "search_read",
            domain=[["product_tmpl_id", "in", tmpl_ids]],
            fields=["product_id", "product_tmpl_id"],
            limit=500,
        )
        # Build tmpl_id → [product_ids] reverse map
        tmpl_to_pids: dict[int, list[int]] = {}
        for pid in product_ids:
            tmpl = tmpl_by_pid.get(pid)
            if tmpl:
                tmpl_to_pids.setdefault(tmpl, []).append(pid)

        kit_ids: set[int] = set()
        for bom in boms:
            if bom.get("product_id"):
                kit_ids.add(bom["product_id"][0])
            else:
                tmpl_id = bom["product_tmpl_id"][0]
                kit_ids.update(tmpl_to_pids.get(tmpl_id, []))
        return kit_ids & set(product_ids)
    except Exception:
        return set()


def fetch_stock_odoo_native(product_ids: list[int]) -> dict[int, dict]:
    """Uses Odoo's built-in computed stock fields — needed for kits where
    stock lives on components and Odoo already aggregates it."""
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


# ── Transit orders (local JSON file) ─────────────────────────────────────────

def read_transit() -> dict:
    if _supabase:
        try:
            rows = _supabase.table("transit_orders").select("*").execute()
            orders = sorted(rows.data or [], key=lambda o: o.get("expected_arrival") or "9999")
            return {"orders": orders}
        except Exception as e:
            print(f"Supabase read error: {e}")
    # Fallback to local file
    if not os.path.exists(TRANSIT_FILE):
        return {"orders": []}
    try:
        with open(TRANSIT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"orders": []}


def write_transit(data: dict) -> None:
    if _supabase:
        try:
            # Full replace: delete all + insert all
            existing = _supabase.table("transit_orders").select("id").execute()
            ids = [r["id"] for r in (existing.data or [])]
            if ids:
                _supabase.table("transit_orders").delete().in_("id", ids).execute()
            if data.get("orders"):
                _supabase.table("transit_orders").insert(data["orders"]).execute()
            return
        except Exception as e:
            print(f"Supabase write error: {e}")
    # Fallback to local file
    with open(TRANSIT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


@app.get("/view/{token}")
def view_readonly(token: str):
    if not VIEW_TOKEN or token != VIEW_TOKEN:
        raise HTTPException(status_code=403, detail="Link inválido o caducado")
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

    # Exclude products that are BoM components (child products of a kit)
    bom_components = fetch_bom_component_ids(product_ids)
    if bom_components:
        product_ids  = [pid for pid in product_ids if pid not in bom_components]
        product_info = {pid: info for pid, info in product_info.items() if pid not in bom_components}

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
    tmpl_by_pid  = {pid: info["tmpl_id"] for pid, info in product_info.items() if info.get("tmpl_id")}
    supplier_map = fetch_supplier_info(list({t for t in tmpl_by_pid.values() if t}))

    all_result_ids = [r["product_id"] for r in results]

    # Identify kit parents — they get Odoo's native computed stock (already accounts for components)
    # Regular products get our ESBO/Stock-filtered query
    kit_ids    = fetch_kit_parent_ids(all_result_ids, tmpl_by_pid)
    kit_ids_l  = list(kit_ids)
    reg_ids    = [pid for pid in all_result_ids if pid not in kit_ids]

    stock_map: dict[int, dict] = {}
    if reg_ids:
        stock_map.update(fetch_stock(reg_ids))
    if kit_ids_l:
        stock_map.update(fetch_stock_odoo_native(kit_ids_l))

    for record in results:
        product_id = record["product_id"]
        stock = stock_map.get(product_id, {})
        virtual_stock = round(stock.get("virtual_available") or 0, 2)

        record["qty_available"]     = round(stock.get("qty_available")  or 0, 2)
        record["virtual_available"] = virtual_stock
        record["incoming_qty"]      = round(stock.get("incoming_qty")   or 0, 2)
        record["outgoing_qty"]      = round(stock.get("outgoing_qty")   or 0, 2)
        record["is_kit"]            = product_id in kit_ids

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


# ── Diagnostic route ─────────────────────────────────────────────────────────

@app.get("/api/debug/missing")
def debug_missing(q: str = ""):
    """Search for products by name across ALL brands/states and explain why they may be missing."""
    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Parámetro ?q= requerido (mínimo 2 caracteres)")

    # Search ALL products matching the name or SKU, including archived
    # Use OR across name and old_sku fields
    all_matches = _rpc("object", "execute_kw", [
        ODOO_DB, _get_uid(), ODOO_PASSWORD,
        "product.product", "search_read",
        [["|", ["name", "ilike", q], ["old_sku", "ilike", q]]],
        {
            "fields": ["id", "display_name", "name", "product_brand_id", "active", "product_tmpl_id", "old_sku"],
            "limit": 50,
            "context": {"active_test": False},
        }
    ])

    if not all_matches:
        return {"query": q, "found": 0, "results": []}

    product_ids  = [p["id"] for p in all_matches]
    tmpl_ids     = [p["product_tmpl_id"][0] for p in all_matches if p.get("product_tmpl_id")]

    # Check which are BoM components
    bom_component_ids = fetch_bom_component_ids(product_ids)

    # Check which are BoM parents
    bom_parent_rows = odoo(
        "mrp.bom", "search_read",
        domain=[["product_tmpl_id", "in", tmpl_ids]],
        fields=["product_tmpl_id", "product_id"],
        limit=200,
    ) if tmpl_ids else []
    bom_parent_tmpl_ids = {b["product_tmpl_id"][0] for b in bom_parent_rows}

    results = []
    for p in all_matches:
        brand_id   = p["product_brand_id"][0] if p.get("product_brand_id") else None
        brand_name = p["product_brand_id"][1] if p.get("product_brand_id") else None
        tmpl_id    = p["product_tmpl_id"][0]  if p.get("product_tmpl_id")  else None
        active     = p.get("active", True)

        reasons_missing = []
        if not active:
            reasons_missing.append("❌ Archivado en Odoo")
        if brand_id not in TARGET_BRAND_IDS:
            reasons_missing.append(f"❌ Marca '{brand_name}' (ID {brand_id}) no está en las marcas monitorizadas {TARGET_BRAND_IDS}")
        if p["id"] in bom_component_ids:
            reasons_missing.append("❌ Es componente de una lista de materiales (BoM) — excluido del pronóstico")
        if tmpl_id in bom_parent_tmpl_ids:
            reasons_missing.append("✅ Es producto padre de kit (BoM parent) — debería aparecer")

        results.append({
            "product_id":   p["id"],
            "name":         p.get("display_name"),
            "sku":          p.get("old_sku") or "",
            "brand_id":     brand_id,
            "brand_name":   brand_name,
            "active":       active,
            "is_bom_component": p["id"] in bom_component_ids,
            "is_bom_parent":    tmpl_id in bom_parent_tmpl_ids,
            "appears_in_forecast": (
                active and
                brand_id in TARGET_BRAND_IDS and
                p["id"] not in bom_component_ids
            ),
            "reasons_missing": reasons_missing if reasons_missing else ["✅ Debería aparecer en el pronóstico"],
        })

    return {"query": q, "found": len(results), "results": results}


# ── Transit order routes ──────────────────────────────────────────────────────

@app.get("/api/transit")
def transit_list():
    return read_transit()


@app.post("/api/transit")
async def transit_create(request: Request):
    body = await request.json()
    data = read_transit()
    new_order = {
        "id":               str(uuid.uuid4()),
        "ref":              body.get("ref", ""),
        "supplier":         body.get("supplier", ""),
        "order_date":       body.get("order_date", ""),
        "expected_arrival": body.get("expected_arrival", ""),
        "created_at":       datetime.now().isoformat(),
        "lines": [
            {
                "id":           str(uuid.uuid4()),
                "product_id":   line.get("product_id"),
                "product_name": line.get("product_name", ""),
                "qty":          float(line.get("qty", 0)),
            }
            for line in body.get("lines", [])
        ],
    }
    data["orders"].append(new_order)
    write_transit(data)
    return new_order


@app.patch("/api/transit/{order_id}")
async def transit_update(order_id: str, request: Request):
    body = await request.json()
    data = read_transit()
    for i, order in enumerate(data["orders"]):
        if order["id"] == order_id:
            data["orders"][i] = {
                **order,
                "ref":              body.get("ref", order.get("ref", "")),
                "supplier":         body.get("supplier", order.get("supplier", "")),
                "order_date":       body.get("order_date", order.get("order_date", "")),
                "expected_arrival": body.get("expected_arrival", order.get("expected_arrival", "")),
                "lines": [
                    {
                        "id":           line.get("id") or str(uuid.uuid4()),
                        "product_id":   line.get("product_id"),
                        "product_name": line.get("product_name", ""),
                        "qty":          float(line.get("qty", 0)),
                    }
                    for line in body.get("lines", order.get("lines", []))
                ],
            }
            write_transit(data)
            return data["orders"][i]
    raise HTTPException(status_code=404, detail="Pedido no encontrado")


@app.delete("/api/transit/{order_id}")
def transit_delete_order(order_id: str):
    data = read_transit()
    data["orders"] = [o for o in data["orders"] if o["id"] != order_id]
    write_transit(data)
    return {"deleted": order_id}


@app.delete("/api/transit/{order_id}/lines/{line_id}")
def transit_delete_line(order_id: str, line_id: str):
    data = read_transit()
    for order in data["orders"]:
        if order["id"] == order_id:
            order["lines"] = [l for l in order["lines"] if l["id"] != line_id]
            if not order["lines"]:
                data["orders"] = [o for o in data["orders"] if o["id"] != order_id]
            break
    write_transit(data)
    return {"deleted": line_id}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
