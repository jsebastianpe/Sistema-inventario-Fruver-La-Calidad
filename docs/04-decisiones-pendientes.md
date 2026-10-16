# Decisiones pendientes del cliente

Ninguna bloquea el inicio de la Fase 2. Mientras no se respondan, se aplica la opción provisional indicada.

| # | Pregunta | Por qué importa | Opción provisional |
|---|---|---|---|
| D1 | ¿En qué ciudad está el negocio y qué central de abastos usa como referencia? | SIPSA publica precios por ciudad y por central (21 ciudades, 80+ centrales), y comparar con otra ciudad induce a error. | Configurable. Ninguna ciudad por defecto. |
| D2 | ¿Dónde se desplegará? (VPS propio, proveedor en la nube, servidor en el local) | Define el costo mensual, el TLS y los respaldos. | VPS Linux con Docker y Caddy. |
| D3 | ¿Cómo repartir transporte y otros costos de una compra: por valor o por peso? | Cambia el costo unitario de cada producto. | Por valor de la línea, con opción por peso en cada compra. |
| D4 | ¿Se venden productos gravados con IVA (no solo frutas y verduras frescas)? | Afecta el precio sugerido y los reportes. El sistema no reemplaza la asesoría contable. | Tarifa de impuesto configurable por producto. 0 % por defecto. |
| D5 | ¿Se permiten ventas con existencias insuficientes (registro posterior de la compra)? | Afecta la operación en mostrador. | Bloqueado. El propietario puede habilitarlo. |
| D6 | Regla de redondeo del precio de venta (¿múltiplo de 50, 100 o 500 COP?) | Afecta los precios sugeridos. | Múltiplo de 50 COP hacia arriba. |
| D7 | ¿Necesitan facturación electrónica DIAN? | Es una integración legal aparte, fuera del alcance inicial. | Fuera de alcance. Las ventas son registros internos. |
| D8 | ~~¿Mover el proyecto fuera de OneDrive e instalar Docker?~~ | **Resuelta (2026-10-16):** proyecto en `C:\dev\sistema-inventario`; Docker Desktop instalado y usado en todas las pruebas. | — |
| D9 | ¿"Libra" = 500 g (uso comercial en Colombia) o 453,59 g (libra internacional)? | Afecta las ventas por libra. | 500 g. Se puede cambiar en la unidad o con una presentación propia por producto. |
| D10 | ¿Quién confirma la unidad de los precios SIPSA y con qué evidencia? | Sin confirmarla, no se compara el costo con el mercado. | Nadie: queda "por confirmar" (ver `08-estado-verificacion-y-pendientes.md`, L1). |
| D11 | Umbral de "variación extraordinaria" del mercado | Con el 20 %, el 4 % de las cotizaciones reales salió marcado. | 20 % configurable; revisar con el propietario. |
