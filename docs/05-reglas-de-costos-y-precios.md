# Reglas de costos, precios e indicadores

Este documento describe **exactamente** lo que calcula el sistema. Cada regla está implementada en un único lugar del backend (indicado entre paréntesis) y cubierta por pruebas automatizadas. El frontend no calcula nada: muestra lo que devuelve el servidor, incluidas las previsualizaciones.

## 1. Tres valores que nunca se confunden

| Valor | Qué es | Dónde se guarda |
|---|---|---|
| **Precio de mercado** | Cotización mayorista publicada por una fuente externa (SIPSA). Es una referencia. | `market_quotes` |
| **Costo real** | Lo que le costó al negocio cada unidad aprovechable, según sus compras. | `stock_balances.avg_cost`, `purchase_lines.unit_cost_base` |
| **Precio de venta** | Lo que se cobra al cliente (vigente o sugerido). | `products.current_price`, `price_suggestions` |

## 2. Unidades y conversiones (`inventory/service.py`, `catalog/service.py`)

* Cada producto lleva su inventario en **una unidad base** (kg, unidad, etc.).
* Cada presentación (bulto, caja, canastilla, libra…) tiene un **factor explícito**: cuántas unidades base contiene. Ejemplos: "Bulto 50 kg" = 50, "Bulto 25 kg" = 25.
* El factor solo se deduce automáticamente entre unidades de masa con equivalencia fija (kg = 1; g = 0,001; libra = 0,5; arroba = 12,5). Para empaques es **obligatorio** indicarlo, porque no todos los bultos pesan lo mismo.
* Conversión en **un solo paso**: `cantidad_base = cantidad × factor`, redondeada a 4 decimales. No se encadenan conversiones.
* Cambiar el factor de una presentación no altera el pasado: cada movimiento guarda su cantidad en unidad base.
* **Libra = 500 g**, que es el uso comercial habitual en Colombia (no la libra internacional de 453,59 g). El propietario puede cambiarlo (ver `04-decisiones-pendientes.md`, D9).

## 3. Costo de una compra (`purchases/calc.py`)

Por línea *i*:

```
bruto_i     = cantidad_i × precio_unitario_i
neto_i      = bruto_i − descuento_línea_i                  (no puede ser negativo)
impuesto_i  = neto_i × tasa_impuesto_i
```

Para la compra:

```
subtotal  = Σ neto_i
impuestos = Σ impuesto_i
adicional = transporte + otros_costos − descuento_global
total     = subtotal + impuestos + adicional                (no puede ser negativo)
```

### Reparto de costos adicionales

El monto `adicional` (transporte + otros costos − descuento global) se reparte entre las líneas:

* **Por valor** (por defecto): en proporción a `neto_i`.
* **Por peso**: en proporción a los kg de cada línea (`cantidad_base × equivalencia en kg`). Solo se permite cuando todos los productos se manejan en unidades de masa.
* La suma de las partes es **exactamente** el monto a repartir. Cada parte se redondea a centavos y el residuo se asigna a la línea de mayor peso. Si todos los pesos son cero, se reparte por igual.

### Costo unitario efectivo

```
costo_línea_i    = neto_i + reparto_i + (impuesto_i, si el impuesto NO es descontable)
cantidad_útil_i  = cantidad_base_i − merma_en_recepción_i   (> 0)
costo_unitario_i = costo_línea_i / cantidad_útil_i          (por unidad base, 6 decimales)
```

* El parámetro **"El IVA de las compras forma parte del costo"** viene activo por defecto (pensado para un negocio que no descuenta IVA). Si el negocio es responsable de IVA y lo descuenta, el propietario lo desactiva. Consulte a su contador.
* La **merma detectada al recibir** no entra al inventario y encarece la cantidad aprovechable.

**Ejemplo verificado en pruebas:** 2 bultos de 50 kg a $80.000 + 30 kg de tomate a $3.000 con $5.000 de descuento de línea; transporte $10.000 y otros costos $2.000, repartidos por valor; merma de 4 kg en la papa.

| | Papa | Tomate |
|---|---|---|
| Neto | 160.000 | 85.000 |
| Reparto de $12.000 | 7.836,73 | 4.163,27 |
| Cantidad útil | 96 kg | 30 kg |
| **Costo unitario efectivo** | **1.748,299271** | **2.972,109000** |

## 4. Valoración del inventario: costo promedio ponderado móvil (`inventory/service.py`)

```
Entrada:  nuevo_promedio = (Q × promedio + q × c) / (Q + q)     (si Q ≤ 0, nuevo_promedio = c)
Salida:   se valora al promedio vigente; el promedio no cambia
Reversión de una entrada (anular compra): se retira al costo original c
          nuevo_promedio = (Q × promedio − q × c) / (Q − q)     (si Q − q ≤ 0 o resultado < 0, se conserva)
Anulación de una venta: la mercancía vuelve al costo con que salió (entrada a ese costo)
```

* Las mermas posteriores a la recepción salen al costo promedio y se reportan como pérdida. No modifican el promedio.
* Cada movimiento guarda el costo aplicado y el saldo y promedio resultantes, así que la historia es reconstruible.
* La **existencia física** (`quantity`), el **costo de gestión** (`avg_cost`) y el **precio de venta** (`current_price`) son campos separados.

## 5. Precio de venta sugerido (`pricing/service.py`)

```
precio_sin_impuesto = costo_unitario / (1 − margen − comisión)
precio_sugerido     = redondeo_comercial( precio_sin_impuesto × (1 + tasa_impuesto) )
```

* `costo_unitario` es el **costo promedio ponderado real**. El mercado **no** sustituye al costo.
* `margen` es el margen bruto objetivo sobre el precio sin impuestos, con 0 ≤ margen < 1. Se toma del producto; si no tiene, de la categoría; si tampoco, el valor general.
* `comisión` representa costos variables proporcionales al precio (por ejemplo, la comisión del datáfono). Debe cumplirse margen + comisión < 1.
* Los precios de venta **incluyen impuestos** y se guardan por unidad base. El impuesto contenido en una venta es `neto − neto / (1 + tasa)`.
* **Redondeo comercial**: primero a centavos y luego al múltiplo configurado (50 COP por defecto) hacia arriba, abajo o al más cercano. El redondeo a centavos evita que residuos como 1699,9999… salten al siguiente múltiplo; esa falla se detectó y se corrigió en las pruebas.
* Se rechazan costos menores o iguales a cero, márgenes fuera de rango y combinaciones con margen + comisión ≥ 1.

Ejemplos (pruebas): costo 1.000 y margen 30 % → 1.428,57 → **1.450**. Con IVA 19 % → **1.700**. Con comisión 5 % → **1.550**.

## 6. Referencias de mercado (`market/service.py`)

Una cotización solo se usa para comparar cuando se cumplen todas estas condiciones:

1. Existe una **correspondencia** revisada por una persona entre el producto interno y el de la fuente, en un mercado (ciudad) concreto, con su factor de conversión.
2. La **unidad del precio publicado** fue confirmada por un usuario. La documentación del SIPSA no la indica.
3. La cotización **no está desactualizada**: tiene como máximo N días (3 por defecto, configurable).

Fórmulas:

* `precio_mercado_por_unidad_base = precio_publicado × factor_conversión`
* `diferencia_costo_vs_mercado = costo_real / precio_mercado − 1`. Un valor positivo significa que se compra por encima del mercado.

Si alguna condición falla, la referencia se muestra con su motivo ("unidad sin confirmar", "desactualizada (10 días)") y **no** entra en ningún cálculo. Nunca se genera una cotización artificial.

**Variación extraordinaria:** `|precio − precio_anterior| / precio_anterior` mayor que el umbral (20 % por defecto), comparando el mismo producto y mercado.

## 7. Política de aprobación de precios

* Las sugerencias **nunca** cambian el precio por sí solas. Las aprueba o rechaza un usuario con el permiso `precios.aprobar` (por defecto, solo el propietario), con justificación obligatoria. Puede aprobar con un precio final distinto.
* Una sugerencia se considera **obsoleta** y no puede aprobarse en estos casos:
  * tiene más de 7 días;
  * el costo del producto cambió más de 1 %;
  * el precio vigente cambió desde que se generó.
* Un cambio manual mayor que el umbral de alerta (15 % por defecto) exige una confirmación explícita.
* Todo cambio queda en `price_history`, que es inmutable para la aplicación. Registra el precio anterior y el nuevo, el origen, la justificación, el usuario y la fecha.
* **Política automática** (desactivada por defecto). Solo el propietario puede habilitarla. Cuando está activa, aplica una sugerencia sin intervención únicamente si se cumplen todas estas condiciones:
  * el producto ya tenía precio (el primer precio siempre es manual);
  * el cambio no supera el límite configurado (10 % por defecto);
  * la sugerencia no tiene ninguna advertencia (por ejemplo, sin referencia de mercado utilizable, costo muy distinto del mercado o variación extraordinaria).

## 8. Indicadores y reportes

| Indicador | Cálculo | Naturaleza |
|---|---|---|
| Valor del inventario | Σ max(existencia, 0) × costo promedio | **Estimación** de gestión |
| Ventas del período | Σ total cobrado de ventas confirmadas (excluye anuladas) | Real (registros internos) |
| Ventas sin impuestos | total − impuesto contenido | Real |
| Costo de lo vendido | Σ cantidad base × costo promedio al momento de vender (instantánea en la línea) | Real según el método de valoración |
| **Margen bruto estimado** | ventas sin impuestos − costo de lo vendido | **No es utilidad neta**: no descuenta arriendo, nómina, servicios ni otros gastos |
| Cobertura (días) | existencia actual / promedio diario vendido del período | Estimación |
| Merma en inventario | Σ movimientos de merma no revertidos, valorados al costo promedio | Real |
| Merma en recepción | Σ merma registrada al recibir compras (su costo ya está en el costo unitario) | Real |

* Las fechas de los reportes se interpretan en hora de Colombia (America/Bogota).
* Las pruebas automatizadas comprueban que los totales de los reportes coinciden con consultas directas a la base de datos.
* El sistema registra ventas internas. **No es facturación electrónica DIAN.**
