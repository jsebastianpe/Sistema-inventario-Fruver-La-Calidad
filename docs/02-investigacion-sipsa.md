# Investigación de la fuente SIPSA (DANE)

Fecha de verificación: **2026-10-16**. Todo lo que aparece como "verificado" se comprobó con llamadas reales al servicio ese día. Ningún dato de esta investigación se cargará en el sistema como dato de demostración.

## Fuentes consultadas

* Página oficial: <https://www.dane.gov.co/index.php/estadisticas-por-tema/agropecuario/sistema-de-informacion-de-precios-sipsa/servicio-web-para-consulta-de-la-base-de-datos-de-sipsa>
* Documentación técnica (PDF, marzo de 2020): <https://www.dane.gov.co/files/investigaciones/agropecuario/sipsa/DANE-webservice-SIPSA.pdf>
* WSDL: `https://appweb.dane.gov.co/sipsaWS/SrvSipsaUpraBeanService?WSDL` y su XSD (`?xsd=1`)

## Hechos verificados

| Aspecto | Resultado |
|---|---|
| Protocolo | SOAP 1.2 (JAX-WS sobre WebLogic). Hay que enviar `Content-Type: application/soap+xml`. |
| Acceso | Público, sin credenciales ni registro. La página oficial no publica condiciones de uso ni límites. |
| HTTPS | Funciona. La URL `http://` del WSDL redirige a HTTPS. |
| Parámetros | **Ninguna operación de precios admite filtros** (ni por fecha, ciudad ni producto). Cada llamada devuelve el conjunto completo. |
| Horario de publicación (oficial) | Datos diarios y semanales desde las 2:00 p. m. del día; mensuales el día 8; abastecimiento el día 10. |
| Contacto técnico | `osistemas@dane.gov.co` (Oficina de Sistemas, DANE) |

### Operaciones y lo que devolvieron el 2026-10-16

| Operación | Contenido | Tamaño / tiempo | Rango de fechas | Volumen |
|---|---|---|---|---|
| `promediosSipsaCiudad` | Precio promedio diario por producto y ciudad (`ciudad, codProducto, producto, fechaCaptura, fechaCreacion, precioPromedio, regId`) | 114 MB / 59 s | 2020-02-03 → **2026-10-15** | 390.491 registros, 33 productos, 21 ciudades |
| `promediosSipsaSemanaMadr` | Mínimo, máximo y promedio semanal por producto y central (`artiId, fuenId, fuenNombre, fechaIni, minimoKg, maximoKg, promedioKg`) | 66 MB / 34 s | 2025-10-18 → 2026-10-03 | 225.829 registros, 351 productos, 80 fuentes |
| `promediosSipsaMesMadr` | Igual que el anterior, mensual | 58 MB / 28 s | 2023-02 → 2026-09 | 194.163 registros, 359 productos, 147 fuentes |
| `promediosSipsaParcial` | Diario por central, con grupo (FRUTAS, VERDURAS…) y departamento | >111 MB; **superó el límite de 120 s** | empieza en 2020-02 | no se completó |
| `promedioAbasSipsaMesMadr` | Abastecimiento en toneladas. No es un precio. La documentación indica que "retorna los registros que no han sido consultados". **No se llamó**, por precaución. | — | — | — |
| `consultarInsumosSipsaMesMadr` | Insumos agrícolas. No aplica. | — | — | — |

### Problemas de calidad detectados en datos reales

1. **Duplicados**: 163 claves `(ciudad, producto, fechaCaptura)` repetidas con distinto `regId` (todas de 2020 y con el mismo precio). El conector deduplica por clave natural.
2. **Nombres de ciudad inconsistentes**: `CÚCUTA` y `SAN JOSÉ DE CÚCUTA` aparecen como ciudades distintas.
3. **Huecos**: el conjunto mensual no tiene el mes 2026-08. Los días sin datos no tienen el mismo número de ciudades (por ejemplo, 13 ciudades el 2026-10-15).
4. **Desfase de publicación**: `fechaCreacion` es posterior a `fechaCaptura`. Lo que se publica el día D corresponde normalmente al día D−1.
5. **Nombres con marcadores**: "Tomate\*", "Aguacate\*". El asterisco agrupa variedades, de modo que no es un producto equivalente a una variedad concreta.
6. **Decimales periódicos**: valores como `1666.666…` (39 dígitos). Se redondean de forma controlada al guardar.

## Pendientes que la documentación pública no resuelve

| # | Pendiente | Consecuencia en el diseño |
|---|---|---|
| P1 | **Unidad de `precioPromedio`** en `promediosSipsaCiudad`: no está documentada. Los campos `*Kg` de las demás operaciones se describen en el PDF como "cantidad recolectada en Kg", aunque por sus valores parecen precios en COP. Además, "Huevo rojo B" muestra `promedioKg = 450`, valor que corresponde a un precio por unidad, no por kg. | La unidad se guarda **por producto de la fuente** y la confirma el administrador al crear la correspondencia. Mientras no se confirme, la cotización se muestra sin compararla con el costo por kg. Se propone preguntar a `osistemas@dane.gov.co` y contrastar con los boletines diarios publicados por el DANE. |
| P2 | Condiciones de uso y límites de frecuencia | Una sola descarga diaria por operación, con reintentos espaciados. Se identifica con un `User-Agent` propio. |
| P3 | Continuidad del servicio (la página lo enmarca en la emergencia sanitaria) | Interfaz `MarketSource` desacoplada para añadir otras fuentes (por ejemplo, archivos oficiales importados manualmente). |
| P4 | Significado de `enviado` y si la consulta modifica estado en el servidor (documentado para abastecimiento) | No se usa `enviado`. No se consulta abastecimiento. |

## Resultado de la integración real (2026-10-16)

El conector implementado (`backend/app/domains/market/connectors/sipsa.py`) se ejecutó contra el servicio real:

| Ejecución | Resultado |
|---|---|
| Primera carga (últimos 60 días) | `exito`: 114.349.449 bytes, 390.656 registros leídos, **12.469 cotizaciones nuevas** (2026-08-17 → 2026-10-16), 0 inválidas, 533 variaciones > 20 %, 72 s |
| Repetición inmediata | `sin_datos_nuevos`: 1.488 registros del período de solapamiento, **0 duplicados** |
| Solicitud desde la API, procesada por el `worker` en Docker | `sin_datos_nuevos`, 0 duplicados |

Cúcuta quedó unificada (20 mercados). Los datos del 2026-10-16 estaban publicados (`fechaCreacion` 14:00 hora de Colombia).

## Diseño del conector (implementado)

* Operación principal: `promediosSipsaCiudad` (diaria y por ciudad, la más cercana a una "cotización del día"). Complemento opcional: `promediosSipsaSemanaMadr`, con más productos y desglose por central.
* Cliente `httpx` con un sobre SOAP mínimo, construido a mano y verificado contra el XSD. No se usa `zeep`, porque cargaría en memoria los 114 MB. La respuesta se lee **en streaming** a un archivo temporal y se procesa con `iterparse` de `defusedxml` para protegerse de XML malicioso.
* Solo se guardan las fechas posteriores a la última `fechaCaptura` ya cargada (más 7 días de margen) y únicamente de las ciudades y los productos configurados. La inserción usa `ON CONFLICT DO NOTHING` sobre `(fuente, ciudad, codProducto, fechaCaptura)`.
* Cada ejecución queda registrada en `market_sync_runs` con inicio, fin, estado, bytes, registros leídos, insertados y descartados, y el error si lo hubo.
* Ejecución diaria a las **15:30 (America/Bogota)**, configurable, con 3 reintentos espaciados (15 min, 45 min, 2 h). Los timeouts son de 30 s para conectar y 300 s para leer.
* Una cotización se marca como **desactualizada** si su `fechaCaptura` tiene más de N días (configurable; 3 por defecto). Las sugerencias de precio nunca se basan en cotizaciones desactualizadas.
