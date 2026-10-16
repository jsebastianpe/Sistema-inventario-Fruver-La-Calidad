# Estado, verificación y pendientes

Fecha: **2026-10-16**.

**Estado:** apto para iniciar un **piloto comercial** en un servidor, una vez resueltos los pendientes marcados como *bloqueante para el piloto*.

* **No** está desplegado en producción: no hay servidor ni dominio del cliente.
* **No** ha pasado una auditoría de seguridad independiente.

Estar preparado para un piloto, estar desplegado en producción y haber superado una auditoría de seguridad son tres estados distintos.

## 1. Cómo se verificó

| Tipo de verificación | Resultado |
|---|---|
| Pruebas automatizadas del backend (`pytest`, contra PostgreSQL real con el rol sin privilegios de la aplicación) | **86 de 86 pasan** |
| Migraciones: `upgrade` → `downgrade base` → `upgrade` y `alembic check` (modelos = esquema) | Sin diferencias |
| Pruebas de interfaz en navegador real (Edge, con Playwright), escritorio 1366 px y teléfono 390 px | 10 secciones sin errores JS ni desbordamiento horizontal |
| Flujo completo por la interfaz contra los contenedores Docker (`e2e/test_flow.py`) | 16 de 16 pasos correctos |
| Integración real con SIPSA (DANE) | Descarga de 114 MB y 390.656 registros; 12.469 cotizaciones cargadas (60 días); 0 inválidas. Repetición: 0 duplicados |
| Stack Docker (db + api + worker) | api *healthy*, worker con latido, contenedores sin privilegios de root |
| Solicitud manual de actualización: API → worker en contenedor → SIPSA | Correcta |
| Respaldo y restauración en una base separada | Datos idénticos; permisos y trigger restaurados |
| `pip-audit` (vulnerabilidades conocidas en dependencias Python) | **0** (se actualizaron Starlette 0.46 → 1.8 y FastAPI) |

## 2. Funcionalidades implementadas y verificadas

**Autenticación y usuarios**
* Contraseñas con Argon2id.
* Sesiones en el servidor, revocables, guardadas solo como hash.
* Cookie `HttpOnly`/`SameSite=Strict` (`Secure` en producción) y token CSRF.
* Expiración por inactividad y absoluta.
* Bloqueo tras 5 intentos fallidos, con el mismo mensaje para usuario inexistente o contraseña errónea.
* Roles propietario, administrador y empleado, más permisos individuales concedidos por el propietario.
* Contraseña temporal obligatoria de cambiar.
* Siempre queda al menos un propietario activo.
* Comando para crear el primer propietario sin credenciales predeterminadas.
* Registro de accesos y auditoría inmutable a nivel de la base de datos.

**Catálogo**
* CRUD de productos con búsqueda, filtros, orden y paginación.
* Desactivación lógica (no hay borrado físico).
* Categorías con margen propio.
* Unidades configurables.
* Presentaciones con conversión explícita a la unidad base.
* El SKU y el código de barras son únicos.

**Inventario**
* Movimientos inmutables: permisos de BD, trigger y reversión compensatoria con un único uso.
* Bloqueo de fila para la concurrencia, verificado con 6 y 8 hilos simultáneos.
* Idempotencia.
* Lotes con salida FEFO y alertas de vencimiento.
* Costo promedio ponderado.
* Mermas con motivo, ajustes, devoluciones y existencia inicial.

**Compras**
* Borrador que no afecta existencias y confirmación transaccional idempotente (4 confirmaciones simultáneas producen una sola entrada).
* Reparto de costos por valor o peso, impuesto configurable como costo y merma en recepción.
* No se admite una factura duplicada por proveedor.
* Anulación trazable, que se rechaza si la mercancía ya salió.
* Previsualización calculada por el servidor.

**Ventas**
* Precio tomado del servidor (el cliente no puede enviar precios) con instantánea de precio y costo.
* Impuesto contenido y descuentos autorizados por rol.
* Atomicidad: si una línea falla, no se registra nada.
* Idempotencia incluso con 5 reintentos simultáneos.
* Anulación que devuelve al mismo costo y a los mismos lotes.
* Los empleados ven solo sus ventas, sin costos.

**Precios de mercado**
* Conector SIPSA real con descarga en flujo y XML seguro (se rechaza XXE).
* Normalización de ciudades.
* Detección de datos ausentes, inválidos, duplicados, desactualizados y de variaciones extraordinarias.
* Reintentos solo ante fallas transitorias.
* Bloqueo contra ejecuciones simultáneas.
* Bitácora de ejecuciones.
* Se conservan las últimas cotizaciones válidas con su fecha original.
* Correspondencias revisadas por personas y unidad confirmada por un usuario.
* Interfaz para incorporar otras fuentes.

**Precios de venta**
* Fórmula con margen, comisión, impuesto y redondeo.
* Sugerencias basadas en el costo real.
* Aprobación o rechazo con justificación.
* Rechazo de sugerencias obsoletas.
* Confirmación para cambios grandes.
* Historial inmutable.
* Política automática desactivada por defecto y protegida.

**Proceso automático**
* Contenedor `worker` independiente del navegador.
* Ejecución diaria a la hora configurable, con puesta al día al arrancar.
* Reintentos a 15, 45 y 120 minutos.
* Solicitudes manuales y latido de monitorización.

**Reportes**
* 12 reportes con exportación CSV y XLSX (con protección contra inyección de fórmulas).
* Panel con indicadores y alertas.
* Los totales coinciden con la base de datos (verificado en pruebas).
* Estimaciones rotuladas como tales; el "margen bruto estimado" no se presenta como utilidad neta.

**Interfaz**
* HTML, CSS y JS sin frameworks, adaptable a teléfono.
* Navegable con teclado y etiquetas accesibles.
* Política CSP estricta, sin `innerHTML` con datos.
* Validación por campo y mensajes en español.
* Confirmación en las operaciones sensibles.

**Despliegue**
* Dockerfile, Compose para desarrollo y producción, y Caddy con HTTPS automático.
* La BD no se expone a internet y se usan roles separados.
* Respaldo verificado y restauración probada.
* Rotación de logs y healthchecks.

## 3. Limitaciones y dependencias externas

| # | Tema | Situación | Impacto |
|---|---|---|---|
| L1 | **Unidad del precio en SIPSA** (`promediosSipsaCiudad`) | La documentación oficial no la indica. El sistema exige que un usuario la confirme por producto; mientras tanto no compara costos con el mercado | **Bloqueante para usar las comparaciones con el mercado.** No bloquea el resto. Acción: consultar a `osistemas@dane.gov.co` o contrastar con los boletines diarios del DANE |
| L2 | Continuidad del servicio SIPSA | Público, sin credenciales ni condiciones publicadas. La página lo enmarca en la emergencia sanitaria de 2020 | Si se retira, el sistema avisa y sigue funcionando con costos reales |
| L3 | Volumen de la consulta | Cada llamada descarga el histórico completo (≈114 MB, ≈70 s) porque el servicio no admite filtros | Se ejecuta una vez al día en el `worker`; no afecta a los usuarios |
| L4 | 533 de 12.469 cotizaciones superaron el umbral de variación del 20 % | Los productos agrícolas varían mucho de un día a otro | Conviene ajustar el umbral con el propietario (configurable) |
| L5 | Facturación electrónica DIAN | Fuera del alcance. Las ventas son registros internos | Si se requiere, es una integración aparte con un proveedor tecnológico autorizado |
| L6 | Tratamiento tributario (IVA de compras como costo, tasas por producto) | Configurable. Valores por defecto provisionales | Validar con el contador |
| L7 | Recuperación de contraseña por correo | No implementada (no hay servicio de correo). La recuperación la hace el propietario con contraseña temporal, o el comando del servidor | Suficiente para un equipo pequeño |
| L8 | Una sola ubicación o bodega | Los traslados entre ubicaciones no están habilitados (el modelo permite añadirlos) | — |
| L9 | Exportación XLSX | Excel usa números binarios; el CSV conserva el valor decimal exacto | Diferencias de redondeo solo en la visualización de Excel |
| L10 | Pruebas en navegadores | Verificado en Edge (Chromium), escritorio y teléfono emulado. No probado en Safari/iOS ni Firefox | Probar en los dispositivos reales del negocio durante el piloto |

## 4. Pendientes

**Bloqueantes para iniciar el piloto**
1. Servidor y dominio del cliente (D2). Desplegar según `06-despliegue-y-operacion.md`.
2. Programar el respaldo diario **y su copia fuera del servidor**.
3. Crear el propietario real con `crear-propietario` y cargar productos, presentaciones y existencias iniciales reales.
4. Definir la ciudad o central de referencia (D1) y crear las correspondencias.

**Antes de producción plena**
5. Confirmar la unidad de los precios SIPSA (L1).
6. Validar con el contador las tasas de impuesto y si el IVA de compras es costo (L6, D4).
7. Ajustar los umbrales de alerta (L4) y el redondeo (D6) con el propietario.
8. Probar en los teléfonos y tabletas del negocio (L10).
9. Configurar un monitor externo de disponibilidad.
10. Considerar una revisión de seguridad independiente antes de exponer el sistema a más usuarios.

**Mejoras posibles (no solicitadas en el alcance actual)**
* Recuperación por correo.
* Varias bodegas.
* Impresión de tirilla de venta.
* Lector de código de barras dedicado.
* Facturación electrónica mediante un proveedor autorizado.

## 5. Estimación de infraestructura y costos operativos

Precios consultados el **2026-10-16**. Lo que no se pudo verificar en la fuente oficial se indica como estimación.

| Concepto | Opción | Costo mensual | Estado del dato |
|---|---|---|---|
| Servidor (VPS) | DigitalOcean Basic, 2 GB RAM / 1 vCPU / 50 GB SSD | **USD 12** | **Verificado** en la página oficial de precios de DigitalOcean |
| Servidor (VPS), con más margen | DigitalOcean Basic, 4 GB / 2 vCPU / 80 GB | **USD 24** | **Verificado** (misma fuente) |
| Respaldo automático del proveedor | DigitalOcean: 20 % (semanal) o 30 % (diario) del costo del droplet → USD 2,40–3,60 sobre el plan de USD 12 | **USD 2,40–3,60** | **Verificado** (porcentaje oficial; cálculo propio) |
| Alternativa europea | Hetzner CPX22 (2 vCPU / 4 GB) ≈ EUR 8 | ≈ EUR 8 | **Estimación sin verificar**: agregadores, tras el alza de abril de 2026; la página oficial no mostró el precio |
| Dominio `.com` o `.co` | Registro anual | ≈ USD 1–3 al mes (≈ USD 12–40 al año) | **Estimación**: varía por registrador |
| Certificado TLS | Let's Encrypt (vía Caddy) | 0 | Gratuito |
| Fuente SIPSA | Servicio público del DANE | 0 | Sin costo publicado ni credenciales (verificado al consultarlo) |
| Almacenamiento externo de respaldos | Bucket de objetos (pocos GB) | ≈ USD 0–5 | **Estimación** |

**Total estimado para el piloto: USD 15–20 al mes** (VPS de 2 GB, respaldo del proveedor y dominio). No incluye impuestos locales ni el tiempo de soporte. Para el VPS se recomienda empezar con 2 GB: la descarga diaria de SIPSA se procesa en flujo y mantiene bajo el uso de memoria.

Fuentes:
* [Precios de Droplets – DigitalOcean](https://www.digitalocean.com/pricing/droplets)
* [Hetzner Cloud](https://www.hetzner.com/cloud)
* [Comparativa de precios de nube 2026-08 (agregador)](https://kimmo.suominen.com/stuff/cpc-2026-08.txt)
