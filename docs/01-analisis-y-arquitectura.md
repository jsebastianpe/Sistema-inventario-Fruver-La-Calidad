# Fase 1 — Análisis del entorno y arquitectura

Fecha: 2026-10-16 · Estado: **propuesta para revisión del cliente**

## 1. Estado real del entorno (verificado)

| Elemento | Estado verificado |
|---|---|
| Carpeta del proyecto | `OneDrive/Desktop/sistema-inventario`, **vacía** (creada el 2026-10-16). No hay código previo que preservar. |
| Python | 3.13.14 |
| PostgreSQL | 18 instalado como servicio de Windows (`postgresql-x64-18`, en ejecución). `psql` no está en el PATH (`C:\Program Files\PostgreSQL\18\bin`). |
| Docker / Docker Compose | **No instalado.** Los archivos Docker se escribirán, pero no podrán probarse en esta máquina hasta instalar Docker Desktop. |
| Git | 2.53 |
| Node | 24.13 (no se necesita: el frontend es HTML/CSS/JS sin compilación) |

Observación: la carpeta está dentro de OneDrive. La sincronización de OneDrive puede bloquear archivos del entorno virtual (`.venv`) y de la caché de pytest. Se recomienda mover el proyecto fuera de OneDrive (por ejemplo `C:\dev\sistema-inventario`) y usar Git como copia de seguridad del código.

## 2. Arquitectura propuesta

Monolito modular con un único repositorio y tres procesos:

```
           Internet (HTTPS)
                │
        ┌───────▼────────┐
        │ Caddy (proxy)  │  TLS automático (Let's Encrypt), cabeceras de seguridad
        └───────┬────────┘
                │ red interna Docker
        ┌───────▼────────┐        ┌──────────────────────┐
        │ api (FastAPI)  │        │ worker (programador) │  actualización de precios SIPSA,
        │ + frontend     │        │ APScheduler          │  alertas y tareas diarias
        │ estático       │        └──────────┬───────────┘
        └───────┬────────┘                   │
                └──────────┬─────────────────┘
                   ┌───────▼────────┐
                   │ PostgreSQL     │  sin puerto publicado a internet
                   └────────────────┘
```

* **api**: FastAPI sirve la API REST (`/api/v1/...`) y los archivos estáticos del frontend, con un solo origen. Así no se necesita CORS en producción; la lista de orígenes permitidos queda vacía por defecto.
* **worker**: un proceso aparte que usa el mismo código de dominio. Ejecuta la descarga de SIPSA, el cálculo de sugerencias y las alertas. Un `pg_advisory_lock` impide que se ejecuten dos actualizaciones a la vez. La descarga de SIPSA tarda más de un minuto y pesa unos 110 MB, y por eso no puede ejecutarse dentro de una petición de usuario.
* **db**: PostgreSQL 16+ con dos roles. `app_owner` ejecuta las migraciones y `app_user` hace las operaciones ordinarias con privilegios DML.

### Estructura de código

```
backend/
  app/
    core/          configuración, seguridad, BD, zona horaria, dinero/decimales, errores
    domains/
      auth/        usuarios, roles, sesiones, auditoría de accesos
      catalog/     productos, categorías, unidades, conversiones
      suppliers/
      purchases/
      sales/
      inventory/   movimientos, lotes, valoración (costo promedio ponderado)
      pricing/     márgenes, sugerencias, aprobación, historial de precios
      market/      fuentes de mercado (interfaz) + conector SIPSA + correspondencias
      reports/
      audit/
    (cada dominio: router.py · schemas.py · models.py · service.py · repository.py)
    worker/        programador y trabajos
  alembic/
  tests/
frontend/          HTML5 + CSS3 + JS (módulos ES, fetch), sin frameworks
deploy/            compose.yaml, Caddyfile, scripts de respaldo/restauración
docs/
```

## 3. Decisiones técnicas (provisionales, justificadas)

| Tema | Decisión | Motivo |
|---|---|---|
| Autenticación | Sesión en servidor (tabla `sessions`). Cookie `HttpOnly`, `Secure` y `SameSite=Strict`, más token CSRF en las peticiones que modifican datos. | Se puede revocar al instante (cierre de sesión, desactivar un usuario, rotar secretos). Es más simple y seguro que un JWT para una app interna con frontend en el mismo origen. |
| Hash de contraseñas | Argon2id (`argon2-cffi`) | Recomendación actual de OWASP. |
| Límite de intentos | Por usuario y por IP, persistido en BD | Funciona aunque haya varias réplicas de `api`. |
| Dinero | `NUMERIC(14,2)` en COP. Costos unitarios en `NUMERIC(18,6)`. `Decimal` en Python. | No se usa coma flotante. Los costos por gramo necesitan más decimales que el peso. |
| Cantidades | `NUMERIC(14,4)` en la unidad base del producto | Admite fracciones de kg sin error binario. |
| Conversiones | Factor explícito por producto y presentación (`1 bulto de papa = 50 kg`), siempre hacia la unidad base, en un solo paso | Evita que se acumulen errores por conversiones en cadena. |
| Valoración | Costo promedio ponderado móvil | Lo pide el MVP. Se recalcula en la transacción de cada entrada. |
| Concurrencia | `SELECT … FOR UPDATE` sobre la fila de existencias, dentro de la transacción | Dos ventas simultáneas no pueden dejar existencias inconsistentes. |
| Idempotencia | Encabezado `Idempotency-Key` obligatorio para confirmar compras y ventas, con restricción `UNIQUE` en BD | Un reintento del navegador no duplica la operación. |
| Inmutabilidad | Los movimientos confirmados no admiten `UPDATE` ni `DELETE`. Se corrigen con movimientos compensatorios. Hay un trigger en BD de respaldo. | Trazabilidad. |
| Existencias negativas | Bloqueadas por defecto. Ajustable por el propietario. | Protege la integridad. |
| Programador | APScheduler en el contenedor `worker`, con hora configurable (`America/Bogota`) | No depende del navegador. Evita el exceso de Celery/Redis. |
| Frontend | HTML, CSS y JS con módulos ES y una pequeña capa `api.js` sobre `fetch` | Lo pide el cliente. Las reglas de negocio solo están en el backend: el frontend muestra lo que el backend calcula. |
| Proxy HTTPS | Caddy | Certificados TLS automáticos y configuración mínima. |

## 4. Reglas de negocio iniciales (a confirmar)

* **Reparto de costos adicionales de una compra** (transporte, otros), por defecto **proporcional al valor de cada línea** (subtotal después de descuentos). Opción alternativa por línea: proporcional al peso en unidad base. Los descuentos globales siguen la misma regla.
* **Costo efectivo**: `(costo de compra + costos atribuidos − descuentos) / cantidad aprovechable`. La merma registrada en recepción reduce la cantidad aprovechable. La merma posterior se registra como movimiento y se reporta como pérdida, sin alterar el costo promedio.
* **Precio sugerido** = `costo efectivo / (1 − margen)`, con `0 ≤ margen < 1`. Después se aplica la regla de redondeo comercial (por ejemplo, al múltiplo de 50 o 100 COP más cercano por encima). Si hay comisiones o impuestos variables expresados como porcentaje del precio `c`: `precio = costo / (1 − margen − c)`, con la condición `margen + c < 1`.
* **Mercado**: solo sirve de referencia. Nunca reemplaza el costo real y nunca se convierte en precio de venta sin aprobación.
* **Margen bruto estimado**: ventas − costo de la mercancía vendida, según el costo promedio vigente al momento de la venta (se guarda en cada línea). No se llamará "utilidad neta".
