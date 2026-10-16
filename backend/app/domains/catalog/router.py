from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import NotFound
from app.core.pagination import Page, PageParams, paginate
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.permissions import P
from app.domains.catalog import service
from app.domains.catalog.models import Category, Product, Unit
from app.domains.catalog.schemas import (
    CategoryIn, CategoryOut, CategoryUpdate, PresentationIn, PresentationOut, PresentationUpdate,
    ProductCostOut, ProductCreate, ProductOut, ProductUpdate, UnitCreate, UnitOut,
)
from app.domains.settings.service import get_commercial_settings

router = APIRouter(tags=["Catálogo"])


@router.get("/unidades", response_model=list[UnitOut])
def list_units(_: CurrentUser = Depends(require(P.PRODUCTOS_VER)), db: Session = Depends(get_db)):
    return db.scalars(select(Unit).order_by(Unit.kind, Unit.name)).all()


@router.post("/unidades", response_model=UnitOut, status_code=201)
def create_unit(data: UnitCreate, cu: CurrentUser = Depends(require(P.CONFIGURACION)),
                db: Session = Depends(get_db)):
    return service.create_unit(db, data, cu)


@router.get("/categorias", response_model=list[CategoryOut])
def list_categories(_: CurrentUser = Depends(require(P.PRODUCTOS_VER)), db: Session = Depends(get_db)):
    return db.scalars(select(Category).order_by(func.lower(Category.name))).all()


@router.post("/categorias", response_model=CategoryOut, status_code=201)
def create_category(data: CategoryIn, cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)),
                    db: Session = Depends(get_db)):
    return service.create_category(db, data, cu)


@router.patch("/categorias/{cat_id}", response_model=CategoryOut)
def update_category(cat_id: int, data: CategoryUpdate,
                    cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)),
                    db: Session = Depends(get_db)):
    return service.update_category(db, cat_id, data, cu)


ProductResp = ProductCostOut | ProductOut


@router.get("/productos", response_model=Page[ProductCostOut | ProductOut])
def list_products(
    q: str | None = Query(None, max_length=100), categoria_id: int | None = None,
    proveedor_id: int | None = None, activo: bool | None = True, bajo_minimo: bool | None = None,
    orden: str = Query("name", pattern="^(name|sku|stock|-stock|updated)$"),
    pg: PageParams = Depends(), cu: CurrentUser = Depends(require(P.PRODUCTOS_VER)),
    db: Session = Depends(get_db),
):
    stmt = service.product_query(q=q, category_id=categoria_id, supplier_id=proveedor_id,
                                 active=activo, low_stock=bajo_minimo, sort=orden)
    page = paginate(db, stmt, pg)
    cfg = get_commercial_settings(db)
    page["items"] = [service.to_out(db, p, cu, cfg) for p in page["items"]]
    return page


@router.get("/productos/{product_id}", response_model=ProductResp)
def get_product(product_id: int, cu: CurrentUser = Depends(require(P.PRODUCTOS_VER)),
                db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None:
        raise NotFound("Producto no encontrado")
    return service.to_out(db, p, cu, get_commercial_settings(db))


@router.post("/productos", response_model=ProductResp, status_code=201)
def create_product(data: ProductCreate, cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)),
                   db: Session = Depends(get_db)):
    p = service.create_product(db, data, cu)
    return service.to_out(db, p, cu, get_commercial_settings(db))


@router.patch("/productos/{product_id}", response_model=ProductResp)
def update_product(product_id: int, data: ProductUpdate,
                   cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)), db: Session = Depends(get_db)):
    p = service.update_product(db, product_id, data, cu)
    return service.to_out(db, p, cu, get_commercial_settings(db))


@router.post("/productos/{product_id}/desactivar", response_model=ProductResp)
def deactivate(product_id: int, cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)),
               db: Session = Depends(get_db)):
    return service.to_out(db, service.set_active(db, product_id, False, cu), cu,
                          get_commercial_settings(db))


@router.post("/productos/{product_id}/activar", response_model=ProductResp)
def activate(product_id: int, cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)),
             db: Session = Depends(get_db)):
    return service.to_out(db, service.set_active(db, product_id, True, cu), cu,
                          get_commercial_settings(db))


@router.post("/productos/{product_id}/presentaciones", response_model=PresentationOut, status_code=201)
def add_presentation(product_id: int, data: PresentationIn,
                     cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)), db: Session = Depends(get_db)):
    pu = service.add_presentation(db, product_id, data, cu)
    return PresentationOut.model_validate(pu).model_copy(update={"unit_code": pu.unit.code})


@router.patch("/productos/{product_id}/presentaciones/{pu_id}", response_model=PresentationOut)
def update_presentation(product_id: int, pu_id: int, data: PresentationUpdate,
                        cu: CurrentUser = Depends(require(P.PRODUCTOS_GESTIONAR)),
                        db: Session = Depends(get_db)):
    pu = service.update_presentation(db, product_id, pu_id, data, cu)
    return PresentationOut.model_validate(pu).model_copy(update={"unit_code": pu.unit.code})
