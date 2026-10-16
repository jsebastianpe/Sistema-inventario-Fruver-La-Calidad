# Manual de usuario

Sistema de gestión comercial para frutas, verduras y otros productos. Funciona desde el navegador de un computador, una tableta o un teléfono.

## 1. Ingreso y seguridad

* Entre a la dirección que le indique el propietario e inicie sesión con su usuario y contraseña.
* Si es su primer ingreso o le asignaron una **contraseña temporal**, el sistema le pedirá cambiarla. La nueva contraseña debe tener al menos 10 caracteres y combinar letras con números o símbolos.
* Después de 5 intentos fallidos el acceso se bloquea 15 minutos.
* La sesión se cierra sola después de 2 horas sin uso (12 horas como máximo). Use **Cerrar sesión** en equipos compartidos.
* ¿Olvidó su contraseña? Pídale al propietario una contraseña temporal en *Usuarios y configuración*.

### Qué puede hacer cada rol

| | Propietario | Administrador | Empleado |
|---|---|---|---|
| Consultar productos y existencias | ✔ | ✔ | ✔ |
| Registrar ventas | ✔ | ✔ | ✔ |
| Registrar mermas | ✔ | ✔ | ✔ |
| Crear y editar productos y proveedores | ✔ | ✔ | — |
| Compras (registrar, confirmar, anular) | ✔ | ✔ | — |
| Ajustes de inventario y reversiones | ✔ | ✔ | — |
| Anular ventas y descuentos mayores al límite | ✔ | ✔ | — |
| Ver costos, márgenes y reportes financieros | ✔ | ✔ | — |
| Aprobar precios de venta | ✔ | — | — |
| Configurar márgenes, redondeo y reglas | ✔ | — | — |
| Usuarios, permisos y auditoría | ✔ | — | — |

El propietario puede conceder permisos adicionales a una persona concreta (por ejemplo, permitir que un administrador apruebe precios).

## 2. Inicio (panel)

Muestra para el período elegido:
* productos activos, existencias bajas y lotes próximos a vencer;
* valor estimado del inventario;
* ventas, compras y margen bruto estimado;
* productos con más movimiento, cambios de precios de mercado y sugerencias pendientes;
* estado de la actualización automática.

El **margen bruto estimado** no es la ganancia neta: no descuenta arriendo, nómina ni servicios.

## 3. Productos

* **+ Nuevo producto**: SKU (código interno), nombre, categoría, **unidad base** (la unidad en que se cuenta el inventario, normalmente kg o unidad) y stock mínimo.
* Después de crearlo, agregue **presentaciones**: bulto, caja, canastilla, libra… Indique **cuántas unidades base contiene cada una**, por ejemplo "Bulto 50 kg" = 50. El sistema no supone pesos: dos bultos distintos se registran como dos presentaciones.
* **Desactivar** oculta el producto de ventas y compras sin borrar su historial.
* Use la búsqueda (nombre, SKU, variedad, código de barras) y los filtros (categoría, proveedor, estado, bajo mínimo).

## 4. Compras

1. **+ Nueva compra**: proveedor, fecha y número de factura.
2. Busque y agregue productos. En cada línea indique presentación, cantidad, precio unitario (sin impuestos), descuento, impuesto, **merma al recibir** (lo que llegó dañado, en unidad base) y fecha de vencimiento.
3. Indique el transporte, otros costos y el descuento global. Elija si se reparten por **valor** o por **peso**.
4. El recuadro **Totales y costo efectivo** muestra el cálculo hecho por el servidor, incluido el costo real por kg o unidad.
5. **Guardar como borrador**: todavía **no** cambia las existencias.
6. **Confirmar compra**: suma al inventario, crea los lotes y actualiza el costo promedio. Una compra confirmada no se edita: si hubo un error, **anúlela** (con motivo) y regístrela de nuevo. Si parte de la mercancía ya se vendió, la anulación se rechaza; en ese caso registre una devolución a proveedor o un ajuste.

## 5. Ventas

1. Pestaña **Registrar venta**: busque el producto por nombre, código o código de barras (también funciona con el teclado: flecha abajo y Enter).
2. Elija la presentación y la cantidad. Se aceptan decimales con coma (2,5). Si aplica un descuento, escríbalo en pesos.
3. El total lo calcula el servidor con el precio vigente. Si no hay existencias suficientes o el descuento supera el máximo autorizado, el botón se bloquea y aparece el motivo.
4. **Registrar venta** y confirme. Si se pierde la conexión justo al registrar, puede volver a intentar sin riesgo: la misma venta no se registra dos veces.
5. **Historial de ventas**: consulte por fechas. Un administrador puede **anular** una venta (con motivo); la mercancía vuelve al inventario.

El precio de cada venta queda guardado tal como se cobró, aunque después cambie el precio del producto. Este registro **no es una factura electrónica** DIAN.

## 6. Inventario

* **Existencias**: cantidad actual y estado frente al mínimo (con costo y valor, si tiene permiso).
* **Movimientos**: todo lo que entró y salió, con fecha, usuario, documento y motivo. Los movimientos no se editan ni se borran. Un movimiento manual equivocado se **revierte** con otro de signo contrario, que también queda registrado.
* **Registrar movimiento**:
  * **Merma / deterioro**: producto dañado o perdido; el motivo es obligatorio.
  * **Ajustes de entrada o salida**: correcciones después de un conteo físico.
  * **Devoluciones** de clientes o a proveedores.
  * **Existencia inicial**: solo para productos que todavía no tienen ningún movimiento.
* **Lotes y vencimientos**: lotes abiertos y su fecha de vencimiento. Al vender o registrar mermas, el sistema descuenta primero lo que vence antes.

## 7. Proveedores

Nombre o razón social, NIT o cédula, contacto, teléfono, correo y observaciones. Un proveedor se puede marcar como inactivo sin perder su historial de compras.

## 8. Precios de mercado (DANE – SIPSA)

* Son **precios mayoristas de referencia** por ciudad, publicados por el DANE. No son el costo de compra del negocio ni precios para el consumidor final.
* **Estado de la actualización**: cuándo se actualizó por última vez, hasta qué fecha hay datos y el resultado de cada ejecución. El botón **Actualizar ahora** pide una actualización inmediata, que tarda 1 o 2 minutos.
* **Productos de la fuente**: el DANE no informa en qué unidad publica estos precios. Antes de usar un producto para comparar costos, el responsable debe **confirmar su unidad**.
* **Referencias por producto**: indique qué producto de la fuente, en qué ciudad, corresponde a cada producto suyo, y con qué factor. No los asocie solo porque el nombre coincide: revise la variedad y la calidad. Los productos con asterisco (\*) agrupan varias variedades.
* Una referencia con más de 3 días se marca como **desactualizada** y no se usa para comparar.

## 9. Precios sugeridos

* **Generar sugerencias** calcula, para cada producto con costo real, el precio que da el margen configurado:
  `costo ÷ (1 − margen − comisión) × (1 + impuesto)`, redondeado (al múltiplo de $50 hacia arriba, por defecto).
* Cada sugerencia muestra el cálculo, la referencia de mercado y las **advertencias**: costo muy distinto del mercado, cambio grande o falta de referencia.
* **Revisar** permite aprobar (con justificación; puede fijar un precio final distinto) o rechazar. Si el costo o el precio cambiaron después de generar la sugerencia, el sistema pide generar una nueva.
* **Historial de precios**: cada cambio, con el precio anterior y el nuevo, quién lo hizo y por qué.
* **Cambiar precio manualmente** (propietario): para cambios de más del 15 % se pide una confirmación adicional.

## 10. Reportes

Elija el reporte, las fechas y, si aplica, la categoría, el proveedor o el producto, y pulse **Generar**. Con permiso puede descargarlo en **Excel** o **CSV**. Reportes disponibles:
* existencias;
* movimientos;
* rotación;
* mermas;
* compras por proveedor;
* ventas por período;
* margen bruto estimado;
* evolución de costos;
* evolución de precios de mercado;
* historial de precios;
* costos vs. mercado;
* auditoría (solo el propietario).

Cada reporte indica qué valores son reales y cuáles son estimaciones.

## 11. Usuarios y configuración

* **Mi cuenta**: cambiar la contraseña.
* **Usuarios y permisos** (propietario): crear usuarios con contraseña temporal, cambiar el rol, desactivar, asignar permisos adicionales y dar contraseñas temporales. Siempre debe quedar al menos un propietario activo.
* **Configuración comercial** (propietario):
  * margen general, comisión y redondeo;
  * umbrales de alerta;
  * descuento máximo sin autorización;
  * si el IVA de las compras es costo;
  * si se permiten existencias negativas;
  * días de alerta de vencimiento;
  * actualización de mercado;
  * política automática de precios.
* **Categorías**: con su margen objetivo propio.
* **Unidades**: kg, g, libra (500 g), arroba, unidad, docena, atado, caja, bulto y canastilla. Puede crear otras.

## 12. Formatos

* Moneda: pesos colombianos (COP).
* Miles con punto y decimales con coma.
* Las fechas y horas se muestran en hora de Colombia.
