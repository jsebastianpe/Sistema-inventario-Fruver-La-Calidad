"""Prueba de humo de la interfaz contra el servidor local (base de datos de desarrollo)."""
import os
import pathlib

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
OUT = pathlib.Path(os.environ.get("E2E_OUT", "e2e/capturas"))
USER = os.environ["E2E_USER"]          # usuario propietario de PRUEBA (nunca de producción)
PW = os.environ["E2E_PASSWORD"]
OUT.mkdir(parents=True, exist_ok=True)
problems = []

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    for name, viewport in [("escritorio", {"width": 1366, "height": 860}), ("movil", {"width": 390, "height": 844})]:
        page = browser.new_page(viewport=viewport, locale="es-CO", timezone_id="America/Bogota")
        page.on("console", lambda m: m.type in ("error", "warning") and problems.append(f"[{name}] consola {m.type}: {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"[{name}] error de página: {e}"))
        page.on("response", lambda r: r.status >= 500 and problems.append(f"[{name}] HTTP {r.status} {r.url}"))
        page.goto(BASE + "/")
        page.fill("input[name=username]", USER)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_selector(".sidebar", timeout=10000)
        sections = ["inicio", "productos", "inventario", "compras", "ventas", "proveedores", "mercado", "precios",
                    "reportes", "administracion"]
        for s in sections:
            page.goto(f"{BASE}/#/{s}")
            page.wait_for_timeout(1200)
            h1 = page.locator("main h1").first.text_content(timeout=5000)
            if page.locator(".notice.danger").count() and s != "mercado":
                problems.append(f"[{name}] {s}: aviso de error visible: {page.locator('.notice.danger').first.text_content()}")
            # Sin desbordamiento horizontal de la página
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if overflow > 2:
                problems.append(f"[{name}] {s}: desbordamiento horizontal de {overflow}px")
            if name == "escritorio" or s in ("inicio", "ventas", "productos"):
                page.screenshot(path=str(OUT / f"{name}_{s}.png"), full_page=False)
            print(f"[{name}] {s}: {h1}")
        page.close()
    browser.close()

print("\nPROBLEMAS:" if problems else "\nSin problemas detectados")
for x in problems:
    print(" -", x)
