# Plan de implementación

> **Estado al 2026-10-16:** las fases 1 a 7 están implementadas y verificadas en desarrollo (ver `08-estado-verificacion-y-pendientes.md`). Se cambió el orden: las fases 4 y 5 se hicieron antes que la interfaz (fase 3), para construir las pantallas una sola vez sobre la API completa. El despliegue en el servidor del cliente está pendiente.

Cada fase termina con pruebas ejecutadas y una lista de lo verificado y lo pendiente. Una fase no se declara terminada sin evidencia.

| Fase | Entregable | Verificación |
|---|---|---|
| **1. Análisis** | Este documento, la arquitectura, la investigación SIPSA y las decisiones pendientes | Revisión del cliente |
| **2a. Base** | Esqueleto FastAPI, configuración por variables de entorno, conexión a PostgreSQL, Alembic, healthcheck, convenciones de errores, utilidades de dinero, decimales y zona horaria | `pytest` contra una BD de pruebas aislada; `alembic upgrade head` desde cero |
| **2b. Autenticación** | Usuarios, roles, sesiones, CSRF, límite de intentos, auditoría, comando `crear-propietario` interactivo (sin contraseña por defecto) | Pruebas de acceso sin sesión, permisos por rol y bloqueo por intentos |
| **2c. Catálogo** | Categorías, unidades, conversiones por producto y presentación, productos (CRUD, búsqueda, filtros, desactivación lógica) | Pruebas de conversión, validación y desactivación |
| **2d. Inventario** | Movimientos inmutables, existencias con bloqueo de fila, lotes y vencimientos, costo promedio ponderado, ajustes y mermas | Pruebas de concurrencia reales (dos conexiones simultáneas), compensaciones e inmutabilidad |
| **2e. Proveedores y compras** | Borrador, confirmación y anulación. Reparto de costos. Idempotencia | Totales, reparto, existencias y costos, reintento duplicado |
| **2f. Ventas** | Venta con instantánea de precio y costo, descuentos autorizados, anulación compensatoria | Precios históricos intactos, existencias, idempotencia |
| **3. Interfaz** | Login, panel, pantallas de cada módulo, tablas con búsqueda, filtros y paginación, diseño adaptable | Recorrido manual de los flujos en el navegador contra la API real |
| **4. Precios** | Interfaz `MarketSource`, conector SIPSA real, correspondencias, calidad de datos, sugerencias, aprobación, historial, trabajo diario en `worker` | Pruebas con respuestas SOAP simuladas (solo en tests) + **una ejecución real documentada** contra SIPSA |
| **5. Reportes** | Reportes, exportación CSV/XLSX, alertas de stock bajo y vencimiento, auditoría | Totales del reporte contrastados con consultas directas a la BD |
| **6. Despliegue** | Dockerfile, compose, Caddy, roles de BD, respaldo y restauración con `pg_dump`, monitoreo de trabajos | Restauración probada en una BD vacía (requiere Docker o el PostgreSQL local) |
| **7. Entrega** | Manuales de usuario, instalación y operación. Lista de funciones verificadas y limitaciones. Estimación de costos | Ejecución completa de la suite de pruebas |

## Riesgos conocidos

1. **SIPSA devuelve el histórico completo en cada llamada** (unos 110 MB). El conector debe procesarlo en streaming y deduplicar. Si el DANE cambia o retira el servicio (la página lo enmarca en la emergencia sanitaria de 2020), la integración se detiene. El sistema sigue operando con costos reales y avisa del fallo.
2. **Unidad de `precioPromedio` no documentada** (ver `02-investigacion-sipsa.md`). Hasta confirmarla, las cotizaciones se guardan con unidad "por confirmar" y no se usan para comparar costos por kg.
3. ~~Docker no está instalado.~~ Resuelto: se instaló Docker Desktop y todas las pruebas usan PostgreSQL 17 en un contenedor, con una base de datos exclusiva para pruebas.
