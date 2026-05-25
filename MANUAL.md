# Odoo Purchasing Intelligence — Manual de usuario

**Versión:** 1.0 · **Stack:** FastAPI + React 18 + Supabase + Odoo v16

---

## ¿Qué es esta herramienta?

Odoo Purchasing Intelligence es una web de análisis y gestión de compras conectada a tu instancia de Odoo. Permite visualizar el stock, pronosticar la demanda, gestionar pedidos en tránsito y crear borradores de órdenes de compra en Odoo directamente desde la web.

**La web es de solo lectura sobre Odoo** (no modifica stock, ventas ni ningún otro dato), con la única excepción de la creación de borradores de órdenes de compra, que es una acción explícita del usuario.

---

## Acceso

| URL | Quién puede entrar | Qué puede hacer |
|---|---|---|
| `/` | Usuario con contraseña | Todo (lectura + creación de pedidos) |
| `/view/<token>` | Enlace compartido (sin contraseña) | Solo lectura, sin crear ni editar |

---

## Secciones de la web

### 1. Dashboard

Vista resumen del estado global del inventario y las compras.

**Qué muestra:**
- **KPIs principales:** Valor total de stock, productos en alerta de stock (<3 meses de cobertura), marcas activas y ventas mensuales estimadas.
- **Gráfico de alertas por marca:** Barras con el número de productos urgentes (<5m de cobertura) agrupados por marca.
- **Pedidos en tránsito:** Resumen de pedidos activos: número total, contenedores enviados a Odoo y pedidos con llegada en menos de 30 días.
- **Distribución de stock por marca:** Gráfico de tarta con el valor de inventario por marca.

---

### 2. Alertas de stock

Lista de todos los productos con stock bajo o cobertura insuficiente.

**Columnas:**
| Columna | Descripción |
|---|---|
| Producto | Nombre y código de proveedor |
| Stock actual | Unidades físicas en el almacén |
| Previsto | Stock actual + entradas pendientes − salidas pendientes |
| Venta/mes | Media de ventas de los últimos 3 meses |
| Cobertura | Meses que dura el stock previsto al ritmo de ventas actual |
| Tendencia | Dirección de las ventas (↑↗→↘↓) |
| En pedido | Unidades pendientes de recibir (Odoo incoming) |

**Filtros:**
- Por marca y por proveedor/fábrica
- Urgencia: Crítico (<3 meses), Urgente (<5 meses), Sin movimiento (sin ventas en 12 meses)
- Buscador por nombre de producto

**Colores de cobertura:**
- 🔴 Rojo — menos de 3 meses (crítico)
- 🟠 Naranja — entre 3 y 5 meses (urgente)
- 🟢 Verde — más de 5 meses (OK)

---

### 3. Pronóstico

La sección principal de planificación de compras.

#### Tabla de pronóstico

| Columna | Descripción |
|---|---|
| Producto | Nombre, código de proveedor y tendencia |
| Stock actual | Unidades físicas |
| + Entradas | Unidades en pedidos Odoo pendientes + pedidos en tránsito (🚢) |
| − Salidas | Unidades comprometidas en pedidos de venta |
| = Previsto | Stock final esperado (ajustado con tránsito) |
| Histórico | Mini-gráfica de ventas de los últimos 12 meses |
| Venta/mes | Media móvil de ventas (últimos 3 meses) |
| Pron. 5m | Unidades proyectadas a vender en los próximos 5 meses (lead time contenedor) |
| Cobertura | Meses de stock previsto ÷ venta mensual |
| Tend. | Icono de tendencia de ventas |
| Precio | Precio del proveedor principal (desde Odoo) |
| Cant. compra | Campo editable donde introduces cuántas unidades quieres pedir |
| Total | Cant. compra × Precio |
| ☑ | Checkbox para selección masiva (ver Crear pedido en tránsito) |

#### Filtros y herramientas

- **Marca / Fábrica:** Filtra la tabla por marca o proveedor.
- **Urgencia:** Botones rápidos para ver solo productos críticos, urgentes o sin movimiento.
- **Ordenar:** Por cobertura (↑ primero los más urgentes) o por volumen de compra.
- **Buscador:** Filtrar por nombre de producto.

#### Ajustar cantidades (IA / reglas)

Escribe una instrucción en lenguaje natural o usa los botones predefinidos:

| Botón | Acción |
|---|---|
| ✨ Rellena | Rellena automáticamente con el déficit estimado + 3 meses de seguridad |
| Aumenta 20% | Multiplica todas las cantidades por 1,2 |
| Reduce 10% | Multiplica todas las cantidades por 0,9 |
| ×1.5 | Multiplica por 1,5 |
| Cajas de 6 | Redondea al múltiplo de 6 más cercano |
| Mínimo 5 | Establece un mínimo de 5 unidades por línea |
| Máximo 100 | Limita a 100 unidades máximo |
| +2 meses seguridad | Añade 2 meses de ventas como stock de seguridad |
| Solo déficit | Solo deja cantidades en los productos que ya tienen stock negativo previsto |

También puedes escribir instrucciones personalizadas como:
- *"aumenta 20% los productos con cobertura menor a 3 meses"*
- *"redondea a cajas de 12"*
- *"solo los productos de la marca X"*

#### Guardar / Borradores

- **💾 Guardar:** Guarda el plan de compra actual como borrador en el navegador (localStorage).
- **📂:** Accede a los borradores guardados y cárgalos de nuevo.

#### Exportar Excel

Exporta la tabla visible a un archivo `.xlsx` con las columnas: Producto, Stock Actual, Previsto, Venta/mes, Pron. 5m, Cobertura, Precio, Cant. a comprar, Total.

#### Resumen por fábrica (barra inferior)

Al introducir cantidades de compra aparece una barra fija en la parte inferior que muestra:
- Total de unidades y valor por fábrica/proveedor
- Expandible para ver el desglose de productos por fábrica

#### Crear pedido en tránsito (selección masiva)

1. Rellena las cantidades de compra en los productos que quieras pedir.
2. Activa los **checkboxes** de los productos deseados (el checkbox del encabezado selecciona todos los que tienen cantidad > 0).
3. Aparece una **barra de selección** en la parte inferior: *"✓ X productos seleccionados — 📦 Crear pedido en tránsito"*.
4. Al pulsar el botón se abre un **modal** con:
   - Selector: crear nuevo pedido o añadir a uno existente.
   - Si es nuevo: Referencia del pedido, Proveedor, Fecha de pedido, Llegada estimada.
   - Lista de productos seleccionados con cantidad editable.
   - Total estimado en euros.
5. Al guardar, el pedido aparece en la sección **En tránsito**.

---

### 4. En tránsito

Gestión de los pedidos realizados que aún no están confirmados en Odoo (pedidos en camino).

#### Lista de pedidos

Cada pedido muestra:
- **Referencia** y **Proveedor/Fábrica**
- **Fecha de pedido** y **Llegada estimada** (con colores):
  - 🔴 Rojo — llegada ya pasada
  - 🟠 Naranja — llegada en menos de 30 días
  - 🟡 Ámbar — llegada en 31–60 días
  - ⬜ Gris — más de 60 días o sin fecha
- **GCPOs vinculadas:** Badges con el nombre de cada orden de compra creada en Odoo. Cada badge tiene un **×** para desvincular solo esa GCPO (restaura las cantidades).
- **Líneas de productos:** Tabla con producto, cantidad original, cantidad pendiente y precio (en USD con cambio a EUR debajo).

#### Acciones por pedido

| Acción | Descripción |
|---|---|
| ✏️ Editar | Modifica ref, proveedor, fechas y cantidades. **Bloqueado si hay GCPOs activas** (debes desvincularnos primero). |
| 📤 Odoo | Abre el modal de contenedor para enviar el pedido (o parte) a Odoo como borrador de PO. |
| 📦 Archivar | Marca el pedido como recibido, registra la fecha real de llegada y lo mueve al historial. |
| 🗑️ Eliminar | Elimina el pedido permanentemente (solo si no tiene GCPOs activas). |

#### Enviar a Odoo (modal de contenedor)

Al pulsar 📤 Odoo:
1. Se abre un modal con todas las líneas del pedido y sus cantidades pendientes.
2. Puedes ajustar la cantidad de cada producto para este contenedor (no puede superar el pendiente).
3. Introduce una **referencia de contenedor** (ej. `CTR-001`).
4. Al confirmar, se crea un **borrador de orden de compra** en Odoo con:
   - Proveedor buscado por nombre en Odoo
   - Productos con sus precios de `product.supplierinfo`
   - Fecha de entrega
5. El pedido de tránsito se actualiza: las cantidades enviadas se restan del pendiente.
6. Aparece un badge con el número de PO de Odoo (ej. `P00224`).

Se pueden enviar **múltiples contenedores** del mismo pedido (hasta que el pendiente llegue a 0).

#### Historial y rendimiento de proveedores

- **Ver historial:** Muestra los pedidos archivados con su fecha real de llegada y desviación respecto a la estimada.
- **Rendimiento por proveedor:** Tabla con la puntualidad media de cada proveedor (calculada de los pedidos archivados).

#### Exportar / Importar Excel

**Exportar:**
Descarga un `.xlsx` con todas las órdenes activas y sus líneas:
- Referencia, Proveedor, Fecha Pedido, Llegada Estimada, Producto, Old SKU, Ref. Proveedor (Odoo), Uds Originales, Uds Pendientes.

**Importar:**
- Sube un Excel con el mismo formato que el exportado.
- La web muestra una **vista previa** con todos los pedidos y productos identificados.
- Productos no encontrados en el catálogo de Odoo aparecen en ámbar y se omiten.
- Al confirmar, se crean todos los pedidos automáticamente.

---

## Datos y actualización

| Dato | Origen | Caché |
|---|---|---|
| Stock, ventas, productos | Odoo v16 (JSON-RPC) | 5 minutos |
| Precios de proveedor | `product.supplierinfo` en Odoo | 5 minutos |
| Pedidos en tránsito | Supabase (PostgreSQL) | Tiempo real |
| Tipos de cambio (USD/EUR) | open.er-api.com | 1 hora |

Para forzar la actualización del caché de pronóstico: botón **actualizar** junto al indicador de caché en la barra de filtros del Pronóstico.

---

## Tipos de cambio

Los precios se muestran en **EUR** (moneda de Odoo). En las columnas de precio de pedidos en tránsito también se muestra la conversión a **USD** como referencia:
- **USD** (línea principal)
- **EUR** (debajo, en gris)

El tipo de cambio se obtiene automáticamente de open.er-api.com y se actualiza cada hora.

---

## Indicador de conexión

En la esquina superior derecha aparece el estado de la conexión con Odoo:
- 🟢 Verde — conectado (muestra la base de datos)
- 🔴 Rojo — sin conexión o error de credenciales

---

## Pendiente / Próximas funciones

- **Auto-archivar tránsito** cuando la GCPO se confirme en Odoo.
- **Notificaciones de llegada** por email/Slack cuando la fecha estimada se acerca.
- **Pagos de contenedor (Container payments):** seguimiento de pagos asociados a cada GCPO/contenedor (depósito, saldo, fecha de pago, estado).
