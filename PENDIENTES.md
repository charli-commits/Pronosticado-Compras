# Pendientes — odoo-purchasing
_Actualizado: 2026-05-27_

## 🔴 Prioritarios
- [ ] **Backup de Supabase** — exportar periódicamente pedidos en tránsito, pagos y archivos adjuntos
- [ ] **Tests automáticos** — endpoints críticos del backend y lógica de forecast
- [ ] **Login multiusuario** — sistema de autenticación para que más personas puedan usar la app

## 🟡 Mejoras pendientes
- [ ] **Cron job ping a Render** — usar cron-job.org para hacer un ping periódico a la URL de Render y evitar que el servidor gratuito se duerma
- [ ] **Auto-archivar pedidos** — cuando se confirma recepción en Odoo, archivar automáticamente el pedido en tránsito
- [ ] **Mejorar y revisar catálogo de productos** — revisión general del catálogo en el pronóstico
- [ ] **Ajustar cálculo de costes** — cuando los consultores hagan el update del valor de inventario en Odoo, revisar y ajustar los costes en la app
- [ ] **Alerta automática de pedidos próximos a llegar** — notificación cuando un pedido en tránsito está a punto de llegar, para gestionar pagos a tiempo

## ✅ Completado hoy (2026-05-27)
- [x] Selector de orden (📦 Llegada / 📅 Pedido / 🕐 Creación) en pestaña En tránsito
- [x] Selector de orden en pestaña Historial
- [x] Precios consistentes: todos los cálculos usan `l.price` guardado (fallback a Odoo)
- [x] Modal de pagos: fallback a priceMap para pedidos sin precio guardado
- [x] Filtro 🆕 Nuevos (<6 meses) en Pronóstico con `create_date` de Odoo
- [x] Fix BoM: quitar "RACK" de separable_boms (TR60 y similares se excluyen bien)
- [x] Añadir SET de vuelta a separable_boms (no se perdieran componentes de sets)
- [x] Exclusión por prefijo SKU: R200-, MG3L-, TS-MX90-, FT90-
- [x] Seguridad: validar path en borrado de archivos (no se puede borrar archivo de otro pedido)
- [x] Eliminar endpoints de debug temporales
- [x] Rename pestaña "Pagos" → "Container Payments"
- [x] Historial: unarchive, sort, cards completas
- [x] Botón Archivar siempre visible en En tránsito
