# Odoo Purchasing Intelligence

App web para análisis de compras y pronóstico de demanda conectada a Odoo v16.

## Stack
- **Backend**: Python 3.11 · FastAPI · Uvicorn
- **Frontend**: React 18 · Chart.js · Tailwind CSS (CDN, sin build step)
- **Conexión**: JSON-RPC a Odoo v16 (solo lectura)

## Funcionalidades
| Pestaña | Descripción |
|---|---|
| 📊 Dashboard | KPIs, tendencia 12 meses, top proveedores, últimas OCs |
| 📦 Alertas de stock | Productos bajo mínimo con severidad y cobertura |
| 🔮 Pronóstico | Suavizado exponencial + tendencia lineal 6 meses, resumen por fábrica, borradores exportables |

---

## Opción A — Levantar en local con Docker (recomendado)

Requisito: tener [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado.

```bash
git clone https://github.com/charli-commits/Pronosticado-Compras.git
cd Pronosticado-Compras
cp .env.example .env        # edita con tus credenciales de Odoo
docker compose up --build
```

Abre **http://localhost:8000**

Para parar:
```bash
docker compose down
```

---

## Opción B — Levantar en local sin Docker (Python)

```bash
git clone https://github.com/charli-commits/Pronosticado-Compras.git
cd Pronosticado-Compras
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # edita con tus credenciales de Odoo
python app.py
```

Abre **http://localhost:8000**

---

## Opción C — Desplegar en Render (servidor gratuito)

Para que tus compañeros puedan acceder desde cualquier sitio sin instalar nada.

### 1. Crear cuenta
Ve a [render.com](https://render.com) y regístrate con tu cuenta de GitHub.

### 2. Nuevo Web Service
Dashboard → **New +** → **Web Service**

### 3. Conectar el repositorio
- Selecciona **"Connect a repository"**
- Busca `charli-commits/Pronosticado-Compras`
- Click **Connect**

### 4. Configurar el servicio
| Campo | Valor |
|---|---|
| Name | `odoo-purchasing-intelligence` |
| Region | Frankfurt EU |
| Branch | `main` |
| Runtime | **Docker** |
| Instance Type | **Free** |

### 5. Variables de entorno
En la sección **"Environment Variables"** añade:

| Key | Value |
|---|---|
| `ODOO_URL` | URL de tu Odoo |
| `ODOO_DB` | Nombre de la base de datos |
| `ODOO_USERNAME` | Usuario de Odoo |
| `ODOO_PASSWORD` | Contraseña o API key |

### 6. Desplegar
Click **"Create Web Service"** y espera 3-5 minutos.

Cuando aparezca **Live** en verde tendrás una URL pública tipo:
`https://odoo-purchasing-intelligence.onrender.com`

> **Nota:** El plan gratuito de Render "duerme" el servidor tras 15 minutos sin actividad. La primera carga puede tardar ~30 segundos en despertar. Para uso interno de equipo pequeño es suficiente.

### Actualizar tras cambios en el código
Render hace deploy automático cada vez que hagas `git push` a `main`. No hay que hacer nada más.

---

## Variables de entorno (.env)

```
ODOO_URL=https://tu-empresa.odoo.com
ODOO_DB=nombre_base_datos
ODOO_USERNAME=usuario@empresa.com
ODOO_PASSWORD=contraseña_o_api_key
# Opcional: HTTP Basic Auth si el servidor tiene protección adicional
ODOO_HTTP_USER=
ODOO_HTTP_PASS=
```

---

## Endpoints API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | Estado de conexión a Odoo |
| GET | `/api/dashboard` | KPIs, tendencia, proveedores, OCs |
| GET | `/api/stock-alerts` | Alertas de reabastecimiento |
| GET | `/api/forecast` | Pronóstico de demanda por producto |
| GET | `/api/forecast/refresh` | Invalida la caché (fuerza recarga desde Odoo) |
