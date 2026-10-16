from fastapi import APIRouter


def include_routers(api: APIRouter) -> None:
    from app.domains.auth.router import router as auth, users_router
    from app.domains.catalog.router import router as catalog
    from app.domains.inventory.router import router as inventory
    from app.domains.market.router import router as market
    from app.domains.pricing.router import router as pricing
    from app.domains.purchases.router import router as purchases
    from app.domains.reports.router import router as reports
    from app.domains.sales.router import router as sales
    from app.domains.settings.router import router as settings
    from app.domains.suppliers.router import router as suppliers

    for r in (auth, users_router, settings, catalog, suppliers, inventory, purchases, sales, pricing, market, reports):
        api.include_router(r)
