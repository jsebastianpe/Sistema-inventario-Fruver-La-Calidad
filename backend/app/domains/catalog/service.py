from decimal import Decimal

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.numbers import cost, money
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser
from app.domains.auth.permissions import P
from app.domains.catalog.models import Category, Product, ProductUnit, Unit, UnitKind
from app.domains.catalog.schemas import (
    CategoryIn, CategoryUpdate, PresentationIn, PresentationOut, PresentationUpdate, ProductCostOut,
    ProductCreate, ProductOut, ProductUpdate, UnitCreate,
)
from app.domains.inventory.models import StockBalance, StockMovement
from app.domains.pricing.models import PriceChangeSource
from app.domains.pricing.service import effective_margin, gross_margin, set_price
from app.domains.settings.service import CommercialSettings
from app.domains.suppliers.models import Supplier

# ------------------------------------------------------------------ unidades


def create_unit(db: Session, data: UnitCreate, cu: CurrentUser) -> Unit:
    if (data.kind == UnitKind.MASA) != (data.kg_equivalent is not None):
        raise AppError("Solo las unidades de masa llevan equivalencia en kg, y es obligatoria para ellas")
    if db.scalar(select(exists().where(Unit.code == data.code))):
        raise Conflict("Ya existe una unidad con ese código")
    unit = Unit(**data.model_dump())
    db.add(unit)
    db.flush()
    audit(db, user_id=cu.id, action="unidades.crear", entity="unidad", entity_id=unit.id,
          details=data.model_dump(), ip=cu.ip)
    db.commit()
    return unit


# ------------------------------------------------------------------ categorías

def create_category(db: Session, data: CategoryIn, cu: CurrentUser) -> Category:
    if data.target_margin is not None and not cu.can(P.CONFIGURACION):
        raise Forbidden("Solo el propietario configura márgenes")
    if db.scalar(select(exists().where(func.lower(Category.name) == data.name.lower()))):
        raise Conflict("Ya existe una categoría con ese nombre")
    cat = Category(name=data.name, target_margin=data.target_margin)
    db.add(cat)
    db.flush()
    audit(db, user_id=cu.id, action="categorias.crear", entity="categoria", entity_id=cat.id,
          details=data.model_dump(), ip=cu.ip)
    db.commit()
    return cat


def update_category(db: Session, cat_id: int, data: CategoryUpdate, cu: CurrentUser) -> Category:
    cat = db.get(Category, cat_id) or _nf("Categoría")
    changes = data.model_dump(exclude_unset=True)
    if "target_margin" in changes and not cu.can(P.CONFIGURACION):
        raise Forbidden("Solo el propietario configura márgenes")
    _apply_changes(db, cat, changes, cu, "categorias.actualizar", "categoria")
    db.commit()
    return cat


# ------------------------------------------------------------------ productos

def _nf(what: str):
    raise NotFound(f"{what} no encontrado(a)")


def _apply_changes(db, obj, changes: dict, cu: CurrentUser, action: str, entity: str) -> dict:
    diff = {}
    for k, v in changes.items():
        if getattr(obj, k) != v:
            diff[k] = {"anterior": getattr(obj, k), "nuevo": v}
            setattr(obj, k, v)
    if diff:
        db.flush()
        audit(db, user_id=cu.id, action=action, entity=entity, entity_id=obj.id, details=diff, ip=cu.ip)
    return diff


def _presentation_factor(db: Session, base_unit: Unit, unit: Unit, factor: Decimal | None) -> Decimal:
    """Factor explícito; solo se deduce entre unidades de masa con equivalencia fija."""
    if base_unit.kind == UnitKind.MASA and unit.kind == UnitKind.MASA:
        derived = cost(unit.kg_equivalent / base_unit.kg_equivalent)
        if factor is not None and cost(factor) != derived:
            raise AppError(
                f"Entre '{unit.name}' y '{base_unit.name}' el factor es {derived.normalize():f}; "
                "si la presentación tiene otro contenido, use una unidad de empaque",
                code="factor_invalido")
        return derived
    if factor is None:
        raise AppError(f"Debe indicar cuántos '{base_unit.code}' contiene la presentación '{unit.name}'",
                       code="factor_requerido")
    if unit.id == base_unit.id and cost(factor) != 1:
        raise AppError("La unidad base siempre tiene factor 1", code="factor_invalido")
    return cost(factor)


def _check_refs(db: Session, *, category_id=None, supplier_id=None, unit_id=None):
    if category_id is not None:
        cat = db.get(Category, category_id)
        if cat is None or not cat.is_active:
            raise AppError("La categoría no existe o está inactiva", code="categoria_invalida")
    if supplier_id is not None:
        sup = db.get(Supplier, supplier_id)
        if sup is None or not sup.is_active:
            raise AppError("El proveedor no existe o está inactivo", code="proveedor_invalido")
    if unit_id is not None:
        unit = db.get(Unit, unit_id)
        if unit is None or not unit.is_active:
            raise AppError("La unidad no existe o está inactiva", code="unidad_invalida")
        return unit
    return None


def _unique_codes(db: Session, sku: str | None, barcode: str | None, exclude_id: int | None = None):
    if sku and db.scalar(select(exists().where(func.lower(Product.sku) == sku.lower(),
                                               Product.id != (exclude_id or 0)))):
        raise Conflict("Ya existe un producto con ese código interno (SKU)")
    if barcode and db.scalar(select(exists().where(Product.barcode == barcode,
                                                   Product.id != (exclude_id or 0)))):
        raise Conflict("Ya existe un producto con ese código de barras")


def create_product(db: Session, data: ProductCreate, cu: CurrentUser) -> Product:
    if data.target_margin is not None and not cu.can(P.CONFIGURACION):
        raise Forbidden("Solo el propietario configura márgenes")
    if data.current_price is not None and not cu.can(P.PRECIOS_APROBAR):
        raise Forbidden("Solo un usuario autorizado puede fijar precios de venta; "
                        "el precio quedará pendiente de aprobación")
    _check_refs(db, category_id=data.category_id, supplier_id=data.usual_supplier_id)
    base_unit = _check_refs(db, unit_id=data.base_unit_id)
    _unique_codes(db, data.sku, data.barcode)

    fields = data.model_dump(exclude={"presentations", "current_price"})
    product = Product(**fields)
    db.add(product)
    db.flush()
    db.add(ProductUnit(product_id=product.id, unit_id=base_unit.id, name=base_unit.name,
                       factor_to_base=Decimal(1), is_base=True))
    db.add(StockBalance(product_id=product.id))
    db.flush()
    for p in data.presentations:
        _add_presentation(db, product, base_unit, p)
    if data.current_price is not None:
        set_price(db, product, data.current_price, source=PriceChangeSource.MANUAL,
                  justification="Precio inicial al crear el producto", user_id=cu.id, ip=cu.ip)
    audit(db, user_id=cu.id, action="productos.crear", entity="producto", entity_id=product.id,
          details=data.model_dump(), ip=cu.ip)
    db.commit()
    db.refresh(product)
    return product


def _add_presentation(db: Session, product: Product, base_unit: Unit, data: PresentationIn) -> ProductUnit:
    unit = _check_refs(db, unit_id=data.unit_id)
    if any(p.name.lower() == data.name.lower() for p in product.presentations):
        raise Conflict(f"El producto ya tiene una presentación llamada '{data.name}'")
    factor = _presentation_factor(db, base_unit, unit, data.factor_to_base)
    pu = ProductUnit(product_id=product.id, unit_id=unit.id, name=data.name, factor_to_base=factor,
                     for_purchase=data.for_purchase, for_sale=data.for_sale)
    db.add(pu)
    db.flush()
    db.refresh(product)
    return pu


def add_presentation(db: Session, product_id: int, data: PresentationIn, cu: CurrentUser) -> ProductUnit:
    product = db.get(Product, product_id) or _nf("Producto")
    pu = _add_presentation(db, product, product.base_unit, data)
    audit(db, user_id=cu.id, action="productos.presentacion_crear", entity="producto",
          entity_id=product.id, details={**data.model_dump(), "factor": pu.factor_to_base}, ip=cu.ip)
    db.commit()
    return pu


def update_presentation(db: Session, product_id: int, pu_id: int, data: PresentationUpdate,
                        cu: CurrentUser) -> ProductUnit:
    pu = db.get(ProductUnit, pu_id)
    if pu is None or pu.product_id != product_id:
        _nf("Presentación")
    changes = data.model_dump(exclude_unset=True)
    if pu.is_base and ({"factor_to_base", "is_active"} & set(changes)):
        raise AppError("La presentación base no se puede desactivar ni cambiar su factor")
    if "factor_to_base" in changes:
        product = db.get(Product, product_id)
        changes["factor_to_base"] = _presentation_factor(db, product.base_unit, pu.unit,
                                                         changes["factor_to_base"])
    # Cambiar el factor no altera el pasado: cada movimiento guarda su cantidad en unidad base.
    _apply_changes(db, pu, changes, cu, "productos.presentacion_actualizar", "presentacion")
    db.commit()
    return pu


def update_product(db: Session, product_id: int, data: ProductUpdate, cu: CurrentUser) -> Product:
    product = db.get(Product, product_id) or _nf("Producto")
    changes = data.model_dump(exclude_unset=True)
    if "target_margin" in changes and not cu.can(P.CONFIGURACION):
        raise Forbidden("Solo el propietario configura márgenes")
    _check_refs(db, category_id=changes.get("category_id"), supplier_id=changes.get("usual_supplier_id"))
    _unique_codes(db, changes.get("sku"), changes.get("barcode"), exclude_id=product.id)
    _apply_changes(db, product, changes, cu, "productos.actualizar", "producto")
    db.commit()
    db.refresh(product)
    return product


def set_active(db: Session, product_id: int, active: bool, cu: CurrentUser) -> Product:
    """Desactivación lógica: los productos nunca se eliminan físicamente (conservan su historial)."""
    product = db.get(Product, product_id) or _nf("Producto")
    _apply_changes(db, product, {"is_active": active}, cu,
                   "productos.activar" if active else "productos.desactivar", "producto")
    db.commit()
    db.refresh(product)
    return product


def product_query(*, q: str | None = None, category_id: int | None = None,
                  supplier_id: int | None = None, active: bool | None = True,
                  low_stock: bool | None = None, sort: str = "name") -> Select:
    stmt = select(Product).join(StockBalance, StockBalance.product_id == Product.id, isouter=True)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like),
                              Product.barcode == q.strip(), func.lower(Product.variety).like(like)))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if supplier_id:
        stmt = stmt.where(Product.usual_supplier_id == supplier_id)
    if active is not None:
        stmt = stmt.where(Product.is_active.is_(active))
    if low_stock:
        stmt = stmt.where(func.coalesce(StockBalance.quantity, 0) <= Product.min_stock,
                          Product.min_stock > 0)
    order = {"name": func.lower(Product.name), "sku": Product.sku,
             "stock": func.coalesce(StockBalance.quantity, 0), "updated": Product.updated_at.desc(),
             "-stock": func.coalesce(StockBalance.quantity, 0).desc()}.get(sort, func.lower(Product.name))
    return stmt.order_by(order, Product.id)


def to_out(db: Session, product: Product, cu: CurrentUser, cfg: CommercialSettings) -> ProductOut:
    bal = db.get(StockBalance, product.id)
    stock = bal.quantity if bal else Decimal(0)
    base = dict(
        id=product.id, sku=product.sku, barcode=product.barcode, name=product.name,
        category_id=product.category_id, category_name=product.category.name,
        variety=product.variety, description=product.description,
        base_unit_id=product.base_unit_id, base_unit_code=product.base_unit.code,
        min_stock=product.min_stock, stock=stock,
        low_stock=product.min_stock > 0 and stock <= product.min_stock,
        current_price=product.current_price, tax_rate=product.tax_rate,
        usual_supplier_id=product.usual_supplier_id, is_perishable=product.is_perishable,
        is_active=product.is_active,
        presentations=[PresentationOut.model_validate(p).model_copy(update={"unit_code": p.unit.code})
                       for p in product.presentations],
        created_at=product.created_at, updated_at=product.updated_at,
    )
    if not cu.can(P.COSTOS_VER):
        return ProductOut(**base)
    avg = bal.avg_cost if bal else Decimal(0)
    return ProductCostOut(
        **base, avg_cost=avg, stock_value=money(max(stock, Decimal(0)) * avg),
        target_margin=product.target_margin, effective_margin=effective_margin(product, cfg),
        gross_margin_at_current_price=gross_margin(product.current_price, avg, product.tax_rate)
        if avg > 0 else None,
    )


def has_history(db: Session, product_id: int) -> bool:
    return bool(db.scalar(select(exists().where(StockMovement.product_id == product_id))))
