# Pendientes — odoo-purchasing
_Actualizado: 2026-05-27_

## 🔴 Prioritarios
- [ ] **Ping a Render** — configurar cron-job.org para hacer ping cada 10 min y evitar que el servidor se duerma (5 min de trabajo)
- [ ] **Auto-archivar pedidos** — cuando el GCPO en Odoo esté en estado "done", archivar automáticamente el pedido en tránsito
- [ ] **Backup de Supabase** — exportar periódicamente pedidos en tránsito, pagos y archivos adjuntos por seguridad

## 🟡 Mejoras pendientes
- [ ] **Ajustar cálculo de costes** — cuando los consultores hagan el update del valor de inventario en Odoo, revisar y ajustar los costes en la app
- [ ] **Mejorar y revisar catálogo de productos** — revisión general del catálogo en el pronóstico (filtros, búsqueda, vista de producto individual)
- [ ] **Rol visualizador** — ya preparado en el código, solo añadir `VIEWER_PASSWORD` en `.env` y Render cuando se necesite
- [ ] **Email diario automático (cron)** — configurar cron-job.org para llamar a `POST /api/alerts/send-email` cada mañana a las 8h (requiere Render despierto → hacer ping primero)

## ✅ Completado (sesiones anteriores + hoy 2026-05-27)

### Autenticación
- [x] Login con contraseña compartida (JWT, 72h de sesión)
- [x] Middleware de auth en todos los endpoints `/api/*`
- [x] Pantalla de login + botón de logout en header
- [x] Soporte de roles (admin / viewer) preparado para el futuro
- [x] Auth desactivada automáticamente en tests/dev (APP_PASSWORD vacío)

### Alertas de pedidos próximos
- [x] Banner naranja en la app cuando hay pedidos llegando en ≤14 días
- [x] Endpoint `POST /api/alerts/send-email` — email HTML via Gmail SMTP
- [x] Botón manual "Enviar email" en el banner

### Tests y CI
- [x] 38 tests automatizados (test_logic.py + test_api.py)
- [x] GitHub Actions: pytest en cada push a cualquier rama

### Pronóstico
- [x] Filtro 🆕 Nuevos (<6 meses) con `create_date` de Odoo
- [x] Exclusión por prefijo SKU: R200-, MG3L-, TS-MX90-, FT90-
- [x] Fix BoM: RACK quitado de separable_boms (TR60 ya no aparece)
- [x] SET añadido de vuelta a separable_boms
- [x] Ordenar por SKU A→Z (además de Cobertura y Volumen)
- [x] Filtro de fábrica solo muestra proveedores con productos visibles
- [x] Normalización de nombres de proveedor ("Empresa, Contacto" → "Empresa")

### En tránsito / Container Payments
- [x] Selector de orden (📦 Llegada / 📅 Pedido / 🕐 Creación)
- [x] Precios consistentes: todos los cálculos usan `l.price` guardado (USD)
- [x] Seguridad: validar path en borrado de archivos
- [x] Botón Archivar siempre visible

### Infraestructura
- [x] Merge `feature/container-payments` → `main`
- [x] Deploy en Render con todas las variables de entorno
- [x] PyJWT añadido a requirements.txt
- [x] Tag `main-backup-pre-merge` para rollback si hace falta
