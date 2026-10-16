"""Matriz de permisos. La autorización SIEMPRE se evalúa en el servidor con esta matriz."""
import enum


class Role(str, enum.Enum):
    PROPIETARIO = "propietario"
    ADMINISTRADOR = "administrador"
    EMPLEADO = "empleado"


class P(str, enum.Enum):
    PRODUCTOS_VER = "productos.ver"
    PRODUCTOS_GESTIONAR = "productos.gestionar"
    INVENTARIO_VER = "inventario.ver"
    INVENTARIO_MERMAS = "inventario.mermas"
    INVENTARIO_AJUSTAR = "inventario.ajustar"
    COSTOS_VER = "costos.ver"
    PROVEEDORES_VER = "proveedores.ver"
    PROVEEDORES_GESTIONAR = "proveedores.gestionar"
    COMPRAS_VER = "compras.ver"
    COMPRAS_REGISTRAR = "compras.registrar"
    COMPRAS_CONFIRMAR = "compras.confirmar"
    COMPRAS_ANULAR = "compras.anular"
    VENTAS_VER = "ventas.ver"
    VENTAS_REGISTRAR = "ventas.registrar"
    VENTAS_ANULAR = "ventas.anular"
    VENTAS_DESCUENTO_AMPLIADO = "ventas.descuento_ampliado"
    MERCADO_VER = "mercado.ver"
    MERCADO_GESTIONAR = "mercado.gestionar"
    PRECIOS_VER = "precios.ver"
    PRECIOS_APROBAR = "precios.aprobar"
    REPORTES_OPERATIVOS = "reportes.operativos"
    REPORTES_FINANCIEROS = "reportes.financieros"
    REPORTES_EXPORTAR = "reportes.exportar"
    CONFIGURACION = "configuracion.gestionar"
    USUARIOS = "usuarios.gestionar"
    AUDITORIA = "auditoria.ver"


_EMPLEADO = {
    P.PRODUCTOS_VER, P.INVENTARIO_VER, P.INVENTARIO_MERMAS, P.PROVEEDORES_VER,
    P.VENTAS_VER, P.VENTAS_REGISTRAR, P.MERCADO_VER, P.REPORTES_OPERATIVOS,
}

_ADMINISTRADOR = _EMPLEADO | {
    P.PRODUCTOS_GESTIONAR, P.INVENTARIO_AJUSTAR, P.COSTOS_VER, P.PROVEEDORES_GESTIONAR,
    P.COMPRAS_VER, P.COMPRAS_REGISTRAR, P.COMPRAS_CONFIRMAR, P.COMPRAS_ANULAR,
    P.VENTAS_ANULAR, P.VENTAS_DESCUENTO_AMPLIADO, P.MERCADO_GESTIONAR, P.PRECIOS_VER,
    P.REPORTES_FINANCIEROS, P.REPORTES_EXPORTAR,
}

ROLE_PERMISSIONS: dict[Role, frozenset[P]] = {
    Role.EMPLEADO: frozenset(_EMPLEADO),
    Role.ADMINISTRADOR: frozenset(_ADMINISTRADOR),
    Role.PROPIETARIO: frozenset(P),
}

# Permisos que el propietario puede conceder individualmente ("autorización expresa").
# La gestión de usuarios no es delegable.
GRANTABLE: frozenset[P] = frozenset(P) - {P.USUARIOS}


def effective_permissions(role: Role, grants: list[str]) -> frozenset[P]:
    extra = {P(g) for g in grants if g in P._value2member_map_ and P(g) in GRANTABLE}
    return ROLE_PERMISSIONS[role] | extra
