import os
import time
import requests
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = FastAPI(title="Odoo Purchasing Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

ODOO_URL = os.getenv("ODOO_URL", "")
ODOO_DB = os.getenv("ODOO_DB", "")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
ODOO_HTTP_USER = os.getenv("ODOO_HTTP_USER", "")
ODOO_HTTP_PASS = os.getenv("ODOO_HTTP_PASS", "")

_session_uid: Optional[int] = None

# Simple in-memory cache for slow endpoints
_cache: dict = {}
CACHE_TTL = 300  # 5 minutes


# ─── Odoo JSON-RPC helpers ────────────────────────────────────────────────────

def _rpc(service: str, method: str, args: list) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "id": 1,
        "params": {"service": service, "method": method, "args": args},
    }
    try:
        http_auth = (ODOO_HTTP_USER, ODOO_HTTP_PASS) if ODOO_HTTP_USER else None
        r = requests.post(
            f"{ODOO_URL}/jsonrpc",
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
            auth=http_auth,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise HTTPException(status_code=400, detail=data["error"].get("data", {}).get("message", str(data["error"])))
        return data.get("result")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail=f"No se puede conectar a Odoo en {ODOO_URL}")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Odoo no respondió a tiempo")


def get_uid() -> int:
    global _session_uid
    if _session_uid:
        return _session_uid
    uid = _rpc("common", "login", [ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD])
    if not uid:
        raise HTTPException(status_code=401, detail="Credenciales de Odoo inválidas")
    _session_uid = uid
    return uid


def execute(model: str, method: str, args: list = None, kwargs: dict = None):
    uid = get_uid()
    return _rpc("object", "execute_kw", [
        ODOO_DB, uid, ODOO_PASSWORD,
        model, method,
        args or [],
        kwargs or {},
    ])


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/api/status")
def status():
    """Check Odoo connectivity."""
    if not ODOO_URL or not ODOO_DB:
        return {"connected": False, "message": "Faltan variables de entorno (.env)"}
    try:
        uid = get_uid()
        return {"connected": True, "uid": uid, "url": ODOO_URL, "db": ODOO_DB}
    except HTTPException as e:
        return {"connected": False, "message": e.detail}


@app.get("/api/dashboard")
def dashboard():
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── KPIs ──
    confirmed_states = ["purchase", "done"]

    def po_amount(domain_extra: list) -> float:
        res = execute("purchase.order", "read_group",
            args=[[*domain_extra]],
            kwargs={
                "fields": ["amount_total:sum"],
                "groupby": [],
            })
        return (res[0]["amount_total"] or 0) if res else 0

    spend_month = po_amount([
        ["state", "in", confirmed_states],
        ["date_approve", ">=", month_start.strftime("%Y-%m-%d %H:%M:%S")],
    ])
    spend_ytd = po_amount([
        ["state", "in", confirmed_states],
        ["date_approve", ">=", year_start.strftime("%Y-%m-%d %H:%M:%S")],
    ])

    def po_count(domain_extra: list) -> int:
        return execute("purchase.order", "search_count", args=[domain_extra])

    orders_month = po_count([
        ["date_order", ">=", month_start.strftime("%Y-%m-%d %H:%M:%S")],
    ])
    draft_count = po_count([["state", "=", "draft"]])
    confirmed_count = po_count([["state", "in", confirmed_states]])

    # products below minimum stock
    low_stock = execute("stock.warehouse.orderpoint", "search_count",
        args=[[["qty_on_hand", "<", "product_min_qty"]]])

    # ── Trend: last 12 months ──
    trend = []
    for i in range(11, -1, -1):
        d = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        m_end = (d.replace(month=d.month % 12 + 1, day=1) if d.month < 12
                 else d.replace(year=d.year + 1, month=1, day=1))
        res = execute("purchase.order", "read_group",
            args=[[
                ["state", "in", confirmed_states],
                ["date_approve", ">=", d.strftime("%Y-%m-%d")],
                ["date_approve", "<", m_end.strftime("%Y-%m-%d")],
            ]],
            kwargs={"fields": ["amount_total:sum"], "groupby": []})
        trend.append({
            "month": d.strftime("%b %Y"),
            "amount": round((res[0]["amount_total"] or 0) if res else 0, 2),
        })

    # ── Top 10 suppliers YTD ──
    supplier_data = execute("purchase.order", "read_group",
        args=[[
            ["state", "in", confirmed_states],
            ["date_approve", ">=", year_start.strftime("%Y-%m-%d")],
        ]],
        kwargs={
            "fields": ["partner_id", "amount_total:sum"],
            "groupby": ["partner_id"],
            "limit": 10,
            "orderby": "amount_total desc",
        })
    top_suppliers = [
        {"name": r["partner_id"][1], "amount": round(r["amount_total"] or 0, 2)}
        for r in (supplier_data or [])
    ]

    # ── Last 20 POs ──
    pos = execute("purchase.order", "search_read",
        args=[[]],
        kwargs={
            "fields": ["name", "partner_id", "date_order", "amount_total", "state", "currency_id"],
            "order": "date_order desc",
            "limit": 20,
        })
    last_orders = [
        {
            "id": r["id"],
            "name": r["name"],
            "supplier": r["partner_id"][1] if r.get("partner_id") else "—",
            "date": (r.get("date_order") or "")[:10],
            "amount": round(r.get("amount_total") or 0, 2),
            "currency": r["currency_id"][1] if r.get("currency_id") else "USD",
            "state": r.get("state", ""),
        }
        for r in (pos or [])
    ]

    return {
        "kpis": {
            "spend_month": round(spend_month, 2),
            "spend_ytd": round(spend_ytd, 2),
            "orders_month": orders_month,
            "draft_count": draft_count,
            "confirmed_count": confirmed_count,
            "low_stock_count": low_stock,
        },
        "trend": trend,
        "top_suppliers": top_suppliers,
        "last_orders": last_orders,
    }


@app.get("/api/stock-alerts")
def stock_alerts():
    now = datetime.now()
    twelve_months_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    # Ubicaciones permitidas (IDs fijos: ESBO/Stock=12, ESBO/Expo Caja=142)
    ALLOWED_LOC_IDS = [12, 142]

    # ── 1. Orderpoints filtrados por ubicación ───────────────────────────────
    orderpoints = execute("stock.warehouse.orderpoint", "search_read",
        args=[[["location_id", "in", ALLOWED_LOC_IDS]]],
        kwargs={
            "fields": [
                "product_id", "location_id",
                "qty_on_hand", "product_min_qty", "product_max_qty",
                "qty_to_order",
            ],
            "limit": 2000,
        })

    # ── 2. Sales volume last 12 months (units sold per product) ─────────────
    sales_raw = execute("sale.order.line", "read_group",
        args=[[
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", twelve_months_ago],
        ]],
        kwargs={
            "fields": ["product_id", "product_uom_qty:sum"],
            "groupby": ["product_id"],
            "limit": 5000,
        })
    sales_by_product = {}
    for row in (sales_raw or []):
        if row.get("product_id"):
            pid = row["product_id"][0]
            sales_by_product[pid] = round(row.get("product_uom_qty") or 0, 2)

    # ── 3. Build alert list ─────────────────────────────────────────────────
    locations_seen = {}
    result = []

    for op in (orderpoints or []):
        on_hand = op.get("qty_on_hand") or 0
        min_qty = op.get("product_min_qty") or 0
        max_qty = op.get("product_max_qty") or 0
        to_order = op.get("qty_to_order") or 0

        if on_hand <= 0:
            severity = "critical"
        elif on_hand < min_qty:
            severity = "warning"
        elif min_qty > 0 and on_hand < min_qty * 1.2:
            severity = "info"
        else:
            severity = "ok"

        coverage = round((on_hand / min_qty * 100) if min_qty > 0 else 100, 1)

        loc_id   = op["location_id"][0] if op.get("location_id") else None
        loc_name = op["location_id"][1] if op.get("location_id") else "—"
        if loc_id:
            locations_seen[loc_id] = loc_name

        pid      = op["product_id"][0] if op.get("product_id") else None
        sales_12m = sales_by_product.get(pid, 0) if pid else 0

        result.append({
            "id": op["id"],
            "product": op["product_id"][1] if op.get("product_id") else "—",
            "location_id": loc_id,
            "location": loc_name,
            "on_hand": round(on_hand, 2),
            "min_qty": round(min_qty, 2),
            "max_qty": round(max_qty, 2),
            "to_order": round(to_order, 2),
            "severity": severity,
            "coverage": min(coverage, 200),
            "sales_12m": sales_12m,
        })

    # ── 4. Sort: severity → sales volume desc (most sold first) ────────────
    sev_order = ["critical", "warning", "info", "ok"]
    result.sort(key=lambda x: (
        sev_order.index(x["severity"]),
        -(x["sales_12m"]),
    ))

    # ── 5. Location list — always show both ESBO locations ──────────────────
    ESBO_LOCATIONS = [
        {"id": 12,  "name": "ESBO/Stock"},
        {"id": 142, "name": "ESBO/Expo Caja"},
    ]

    return {"alerts": result, "locations": ESBO_LOCATIONS}


TARGET_BRAND_IDS = [52, 39, 45]   # Titanium Strength, Force USA, Nordictrack

@app.get("/api/forecast/refresh")
def forecast_refresh():
    """Force-clear the forecast cache so next /api/forecast re-fetches from Odoo."""
    _cache.pop("forecast", None)
    return {"cleared": True}

@app.get("/api/forecast")
def forecast():
    # Return cached result if fresh enough
    cached = _cache.get("forecast")
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["data"]

    now = datetime.now()
    twelve_months_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    LEAD_MONTHS = 6   # container lead time: plan 6 months ahead

    # ── 1. All product variants for target brands ────────────────────────────
    variants_raw = execute("product.product", "search_read",
        args=[[["product_brand_id", "in", TARGET_BRAND_IDS]]],
        kwargs={
            "fields": ["id", "product_tmpl_id", "product_brand_id", "old_sku", "display_name"],
            "limit": 5000,
        })

    if not variants_raw:
        return {"products": [], "brands": []}

    brand_by_product: dict = {}
    for v in variants_raw:
        pid = v["id"]
        brand_by_product[pid] = {
            "brand_id":   v["product_brand_id"][0] if v.get("product_brand_id") else None,
            "brand_name": v["product_brand_id"][1] if v.get("product_brand_id") else "Sin marca",
            "old_sku":    v.get("old_sku") or "",
            "name":       v.get("display_name") or "",
            "tmpl_id":    v["product_tmpl_id"][0] if v.get("product_tmpl_id") else None,
        }

    target_pids = list(brand_by_product.keys())

    # ── 2. Sales history last 12 months (base signal for forecast) ───────────
    sale_lines = execute("sale.order.line", "search_read",
        args=[[
            ["product_id", "in", target_pids],
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", twelve_months_ago],
        ]],
        kwargs={"fields": ["product_id", "product_uom_qty", "order_id"], "limit": 50000})

    sale_order_ids = list({l["order_id"][0] for l in (sale_lines or []) if l.get("order_id")})
    sale_orders_raw = execute("sale.order", "search_read",
        args=[[["id", "in", sale_order_ids]]],
        kwargs={"fields": ["id", "date_order"], "limit": len(sale_order_ids) + 1})
    sale_order_dates = {
        o["id"]: o["date_order"][:7]
        for o in (sale_orders_raw or []) if o.get("date_order")
    }

    sales_by_month: dict = defaultdict(lambda: defaultdict(float))
    for line in (sale_lines or []):
        if not line.get("product_id") or not line.get("order_id"):
            continue
        pid  = line["product_id"][0]
        mkey = sale_order_dates.get(line["order_id"][0], "")
        if mkey:
            sales_by_month[pid][mkey] += line.get("product_uom_qty") or 0

    # ── 3. PO history — for vendor name + avg purchase price ─────────────────
    po_lines = execute("purchase.order.line", "search_read",
        args=[[
            ["order_id.state", "in", ["purchase", "done"]],
            ["order_id.date_approve", ">=", twelve_months_ago],
            ["product_id", "in", target_pids],
        ]],
        kwargs={"fields": ["product_id", "product_qty", "price_subtotal", "order_id"], "limit": 10000})

    po_order_ids = list({l["order_id"][0] for l in (po_lines or []) if l.get("order_id")})
    po_orders_raw = execute("purchase.order", "search_read",
        args=[[["id", "in", po_order_ids]]],
        kwargs={"fields": ["id", "partner_id"], "limit": len(po_order_ids) + 1})
    po_vendor_map = {o["id"]: o["partner_id"][1] for o in (po_orders_raw or []) if o.get("partner_id")}

    vendor_votes: dict = defaultdict(lambda: defaultdict(int))
    price_data:   dict = defaultdict(lambda: {"qty": 0.0, "amount": 0.0})
    for line in (po_lines or []):
        if not line.get("product_id"):
            continue
        pid = line["product_id"][0]
        oid = line["order_id"][0]
        vendor = po_vendor_map.get(oid)
        if vendor:
            vendor_votes[pid][vendor] += 1
        price_data[pid]["qty"]    += line.get("product_qty") or 0
        price_data[pid]["amount"] += line.get("price_subtotal") or 0

    def dominant_vendor(pid: int) -> str:
        votes = vendor_votes.get(pid, {})
        return max(votes, key=votes.__getitem__) if votes else "—"

    def avg_unit_price(pid: int) -> float:
        d = price_data.get(pid, {})
        q = d.get("qty", 0)
        return round(d.get("amount", 0) / q, 4) if q > 0 else 0.0

    # ── 4. Build forecast per product (based on sales velocity) ─────────────
    months_timeline = []
    for i in range(11, -1, -1):
        d = now - timedelta(days=i * 30)
        months_timeline.append(d.strftime("%Y-%m"))

    results = []
    for pid in target_pids:
        qty_series = [sales_by_month[pid].get(m, 0.0) for m in months_timeline]
        binfo = brand_by_product[pid]

        # No sales in 12 months → dead stock candidate, include with no_movement flag
        if sum(qty_series) == 0:
            results.append({
                "product_id":         pid,
                "product_name":       binfo["name"],
                "old_sku":            binfo["old_sku"],
                "brand_id":           binfo["brand_id"],
                "brand_name":         binfo["brand_name"],
                "history":            [{"month": m, "qty": 0.0} for m in months_timeline],
                "forecast":           [],
                "trend":              "stable",
                "monthly_velocity":   0.0,
                "total_forecast_qty": 0.0,
                "avg_price":          avg_unit_price(pid),
                "no_movement":        True,
                "no_recent_sales":    True,
            })
            continue

        # Detect slow movers: zero sales in the last 3 months
        no_recent_sales = all(qty_series[i] == 0 for i in [-1, -2, -3])

        # Exponential smoothing α=0.3
        alpha = 0.3
        smoothed = [qty_series[0]]
        for q in qty_series[1:]:
            smoothed.append(alpha * q + (1 - alpha) * smoothed[-1])

        # Linear trend over last 6 months
        recent = smoothed[-6:]
        n = len(recent)
        if n >= 2:
            x_mean = (n - 1) / 2
            y_mean  = sum(recent) / n
            numer   = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(recent))
            denom   = sum((i - x_mean) ** 2 for i in range(n))
            slope   = numer / denom if denom else 0
        else:
            slope = 0

        base = smoothed[-1]
        # Monthly velocity = average of last 3 smoothed months
        monthly_velocity = sum(smoothed[-3:]) / 3

        # Project LEAD_MONTHS forward (6 months = full container cycle)
        forecast_Nm = []
        for i in range(1, LEAD_MONTHS + 1):
            d = now + timedelta(days=i * 30)
            forecast_Nm.append({
                "month": d.strftime("%b %Y"),
                "qty":   round(max(0.0, base + slope * i), 2),
            })

        total_forecast_qty = sum(f["qty"] for f in forecast_Nm)
        trend = "up" if slope > 0.5 else ("down" if slope < -0.5 else "stable")

        history_out = [
            {"month": m, "qty": round(qty_series[i], 2)}
            for i, m in enumerate(months_timeline)
        ]

        results.append({
            "product_id":         pid,
            "product_name":       binfo["name"],
            "old_sku":            binfo["old_sku"],
            "brand_id":           binfo["brand_id"],
            "brand_name":         binfo["brand_name"],
            "history":            history_out,
            "forecast":           forecast_Nm,
            "trend":              trend,
            "monthly_velocity":   round(monthly_velocity, 2),
            "total_forecast_qty": round(total_forecast_qty, 2),
            "avg_price":          avg_unit_price(pid),
            "no_movement":        False,
            "no_recent_sales":    no_recent_sales,
        })

    results.sort(key=lambda x: x["total_forecast_qty"], reverse=True)

    # ── 5. Stock quantities ──────────────────────────────────────────────────
    all_pids = [r["product_id"] for r in results]
    stock_raw = execute("product.product", "search_read",
        args=[[["id", "in", all_pids]]],
        kwargs={
            "fields": ["id", "qty_available", "virtual_available", "incoming_qty", "outgoing_qty"],
            "limit": len(all_pids) + 1,
        })
    stock_map = {s["id"]: s for s in (stock_raw or [])}

    # ── 6. Vendor code + list price from supplierinfo ────────────────────────
    pid_to_tmpl = {pid: brand_by_product[pid]["tmpl_id"] for pid in all_pids
                   if brand_by_product.get(pid, {}).get("tmpl_id")}
    all_tmpl_ids = list(set(v for v in pid_to_tmpl.values() if v))
    supplier_raw = execute("product.supplierinfo", "search_read",
        args=[[["product_tmpl_id", "in", all_tmpl_ids]]],
        kwargs={
            "fields": ["product_tmpl_id", "partner_id", "price", "product_code", "sequence"],
            "order": "sequence asc",
            "limit": len(all_tmpl_ids) * 3,
        })
    supplierinfo_by_tmpl: dict = {}
    for sup in (supplier_raw or []):
        tid = sup["product_tmpl_id"][0]
        if tid not in supplierinfo_by_tmpl:
            supplierinfo_by_tmpl[tid] = {
                "vendor_code":  sup.get("product_code") or "",
                "vendor_price": round(sup.get("price") or 0, 4),
            }

    # ── 7. Enrich: stock + vendor + suggested order ──────────────────────────
    for r in results:
        pid = r["product_id"]
        s   = stock_map.get(pid, {})
        virtual = round(s.get("virtual_available") or 0, 2)

        r["qty_available"]     = round(s.get("qty_available")    or 0, 2)
        r["virtual_available"] = virtual
        r["incoming_qty"]      = round(s.get("incoming_qty")     or 0, 2)
        r["outgoing_qty"]      = round(s.get("outgoing_qty")     or 0, 2)

        # Months of coverage = virtual stock ÷ monthly sales velocity
        vel = r["monthly_velocity"]
        r["coverage_months"] = round(virtual / vel, 1) if vel > 0 else 99.0

        # Suggested order = units needed to cover 6 months of demand
        # Use max(smoothed_forecast, velocity × LEAD_MONTHS) as demand floor.
        # This prevents the smoothing from under-predicting when recent months are
        # low but current velocity still shows real demand (e.g. declining trend
        # products that still sell 10+ units/month).
        velocity_demand = r["monthly_velocity"] * LEAD_MONTHS
        demand_estimate = max(r["total_forecast_qty"], velocity_demand)
        r["suggested_qty"] = int(max(0.0, round(demand_estimate - virtual, 0)))

        # Vendor from actual purchase history
        r["vendor"] = dominant_vendor(pid)

        tid = pid_to_tmpl.get(pid)
        si  = supplierinfo_by_tmpl.get(tid, {}) if tid else {}
        r["vendor_code"]  = si.get("vendor_code", "")
        r["vendor_price"] = si.get("vendor_price") or r["avg_price"]

    brands = [
        {"id": 52, "name": "Titanium Strength"},
        {"id": 39, "name": "Force USA"},
        {"id": 45, "name": "Nordictrack"},
    ]

    result = {"products": results, "brands": brands}
    _cache["forecast"] = {"data": result, "ts": time.time()}
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
