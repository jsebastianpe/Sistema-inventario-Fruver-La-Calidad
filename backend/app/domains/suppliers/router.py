from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Conflict, NotFound
from app.core.pagination import Page, PageParams, paginate
from app.core.schemas import Name
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.permissions import P
from app.domains.suppliers.models import Supplier

router = APIRouter(prefix="/proveedores", tags=["Proveedores"])


class SupplierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    tax_id: str | None = Field(None, pattern=r"^[0-9]{5,15}(-[0-9])?$", description="NIT o cédula")
    contact_name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, pattern=r"^[0-9 +()-]{7,40}$")
    email: EmailStr | None = None
    address: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)


class SupplierUpdate(SupplierIn):
    name: Name | None = None
    is_active: bool | None = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    tax_id: str | None
    contact_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _dup_tax_id(db: Session, tax_id: str | None, exclude: int = 0):
    if tax_id and db.scalar(select(exists().where(Supplier.tax_id == tax_id, Supplier.id != exclude))):
        raise Conflict("Ya existe un proveedor con ese NIT/cédula")


@router.get("", response_model=Page[SupplierOut])
def list_suppliers(q: str | None = Query(None, max_length=100), activo: bool | None = True,
                   pg: PageParams = Depends(), _: CurrentUser = Depends(require(P.PROVEEDORES_VER)),
                   db: Session = Depends(get_db)):
    stmt = select(Supplier)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Supplier.name).like(like), Supplier.tax_id == q,
                              func.lower(Supplier.contact_name).like(like)))
    if activo is not None:
        stmt = stmt.where(Supplier.is_active.is_(activo))
    return paginate(db, stmt.order_by(func.lower(Supplier.name)), pg)


@router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int, _: CurrentUser = Depends(require(P.PROVEEDORES_VER)),
                 db: Session = Depends(get_db)):
    s = db.get(Supplier, supplier_id)
    if s is None:
        raise NotFound("Proveedor no encontrado")
    return s


@router.post("", response_model=SupplierOut, status_code=201)
def create_supplier(data: SupplierIn, cu: CurrentUser = Depends(require(P.PROVEEDORES_GESTIONAR)),
                    db: Session = Depends(get_db)):
    _dup_tax_id(db, data.tax_id)
    s = Supplier(**data.model_dump())
    db.add(s)
    db.flush()
    audit(db, user_id=cu.id, action="proveedores.crear", entity="proveedor", entity_id=s.id,
          details=data.model_dump(), ip=cu.ip)
    db.commit()
    return s


@router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: int, data: SupplierUpdate,
                    cu: CurrentUser = Depends(require(P.PROVEEDORES_GESTIONAR)),
                    db: Session = Depends(get_db)):
    s = db.get(Supplier, supplier_id)
    if s is None:
        raise NotFound("Proveedor no encontrado")
    changes = data.model_dump(exclude_unset=True)
    _dup_tax_id(db, changes.get("tax_id"), exclude=s.id)
    diff = {}
    for k, v in changes.items():
        if getattr(s, k) != v:
            diff[k] = {"anterior": getattr(s, k), "nuevo": v}
            setattr(s, k, v)
    if diff:
        audit(db, user_id=cu.id, action="proveedores.actualizar", entity="proveedor", entity_id=s.id,
              details=diff, ip=cu.ip)
    db.commit()
    return s
