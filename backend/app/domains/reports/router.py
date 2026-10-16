from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.jsonsafe import exact
from app.core.errors import Forbidden
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.permissions import P
from app.domains.reports import export, service

router = APIRouter(tags=["Reportes"])


@router.get("/panel")
def dashboard(desde: date | None = None, hasta: date | None = None,
              cu: CurrentUser = Depends(require(P.PRODUCTOS_VER)), db: Session = Depends(get_db)):
    d0, h0 = service.default_period()
    return exact(service.dashboard(db, cu, desde or d0, hasta or h0))


@router.get("/alertas")
def alerts(cu: CurrentUser = Depends(require(P.INVENTARIO_VER)), db: Session = Depends(get_db)):
    return exact(service.alerts(db, cu))


@router.get("/reportes")
def catalog(cu: CurrentUser = Depends(require(P.PRODUCTOS_VER))):
    """Reportes disponibles para el usuario actual."""
    return [{"nombre": name, "permisos": [p.value for p in perms]}
            for name, (_, perms) in service.REGISTRY.items() if all(cu.can(p) for p in perms)]


@router.get("/reportes/{nombre}")
def report(nombre: str, desde: date | None = None, hasta: date | None = None,
           product_id: int | None = None, category_id: int | None = None, supplier_id: int | None = None,
           mercado: str | None = Query(None, max_length=120),
           formato: str = Query("json", pattern="^(json|csv|xlsx)$"),
           cu: CurrentUser = Depends(require(P.PRODUCTOS_VER)), db: Session = Depends(get_db)):
    d0, h0 = service.default_period()
    params = service.Params(desde=desde or d0, hasta=hasta or h0, product_id=product_id,
                            category_id=category_id, supplier_id=supplier_id, mercado=mercado)
    rep = service.run(db, cu, nombre, params)
    if formato == "json":
        return exact({"titulo": rep.title, "filtros": rep.filters, "notas": rep.notes,
                      "columnas": [asdict(c) for c in rep.columns], "filas": rep.rows, "totales": rep.totals})
    if not cu.can(P.REPORTES_EXPORTAR):
        raise Forbidden("No tiene permiso para exportar reportes", details=[P.REPORTES_EXPORTAR.value])
    audit(db, user_id=cu.id, action="reportes.exportar", entity="reporte", entity_id=nombre,
          details={"formato": formato, **rep.filters}, ip=cu.ip)
    db.commit()
    filename = f"{nombre}_{params.desde}_{params.hasta}.{formato}"
    if formato == "csv":
        return Response(export.to_csv(rep), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return Response(export.to_xlsx(rep, cu.user.full_name),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
