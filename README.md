# Odoo Purchasing Intelligence

App web standalone para análisis avanzado de compras en Odoo v16.

## Stack
- **Backend**: Python 3.11+ · FastAPI · Uvicorn
- **Frontend**: React 18 · Recharts · Tailwind CSS (todo vía CDN, sin build step)
- **Conexión**: JSON-RPC a `/jsonrpc` de Odoo v16

## Módulos
| Pestaña | Descripción |
|---|---|
| 📊 Dashboard | KPIs, tendencia 12 meses, top 10 proveedores, últimas 20 OCs |
| 📦 Alertas de stock | Productos bajo mínimo con severidad y barra de cobertura |
| 🔮 Pronóstico | Suavizado exponencial + tendencia lineal, recomendación 3 meses |

## Setup rápido

```bash
cd odoo-purchasing
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # edita con tus credenciales
python app.py
```

Abre http://localhost:8000

## Variables de entorno (.env)

```
ODOO_URL=https://tu-empresa.odoo.com
ODOO_DB=nombre_base_datos
ODOO_USERNAME=usuario@empresa.com
ODOO_PASSWORD=contraseña
```

## Endpoints API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | Estado de conexión a Odoo |
| GET | `/api/dashboard` | KPIs, tendencia, proveedores, OCs |
| GET | `/api/stock-alerts` | Alertas de reabastecimiento |
| GET | `/api/forecast` | Pronóstico de demanda por producto |
