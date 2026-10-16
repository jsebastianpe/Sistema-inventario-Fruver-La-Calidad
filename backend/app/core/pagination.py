from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
        self.page = page
        self.page_size = page_size


def paginate(db: Session, stmt: Select, params: PageParams, *, scalars: bool = True) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    q = stmt.limit(params.page_size).offset((params.page - 1) * params.page_size)
    items = db.scalars(q).all() if scalars else db.execute(q).all()
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}
