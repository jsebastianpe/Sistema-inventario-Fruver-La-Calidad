"""Flujo completo por la interfaz (datos marcados como PRUEBA en la base de desarrollo)."""
import os
import pathlib
import re
import uuid

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
OUT = pathlib.Path(os.environ.get("E2E_OUT", "e2e/capturas"))
USER = os.environ["E2E_USER"]          # usuario propietario de PRUEBA (nunca de producción)
PW = os.environ["E2E_PASSWORD"]
TAG = uuid.uuid4().hex[:4].upper()
problems = []


def step(msg):
    print("✔", msg)


def dialog(page):
    return page.locator("dialog[open]").last


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="es-CO", timezone_id="America/Bogota",
                            accept_downloads=True)
    page.on("pageerror", lambda e: problems.append(f"error de página: {e}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"HTTP {r.status} {r.url}"))
    page.set_default_timeout(10000)

    page.goto(BASE + "/")
    page.fill("input[name=username]", USER)
    page.fill("input[name=password]", PW)
    page.click("button[type=submit]")
    page.wait_for_selector(".sidebar")
    step("Inicio de sesión")

    # Categoría
    page.goto(BASE + "/#/administracion")
    page.get_by_role("tab", name="Categorías").click()
    page.fill("form input[name=name]", f"Frutas (prueba {TAG})")
    page.fill("form input[name=target_margin]", "0,30")
    page.get_by_role("button", name="Crear").click()
    expect(page.get_by_text(f"Frutas (prueba {TAG})")).to_be_visible()
    step("Categoría creada con margen 30 %")

    # Proveedor
    page.goto(BASE + "/#/proveedores")
    page.get_by_role("button", name="+ Nuevo proveedor").click()
    d = dialog(page)
    d.locator("input[name=name]").fill(f"Proveedor de prueba {TAG}")
    d.locator("input[name=phone]").fill("300 000 0000")
    d.get_by_role("button", name="Guardar").click()
    expect(page.locator("dialog[open]")).to_have_count(0)
    step("Proveedor creado")

    # Producto con validación de errores
    page.goto(BASE + "/#/productos")
    page.get_by_role("button", name="+ Nuevo producto").click()
    d = dialog(page)
    d.get_by_role("button", name="Crear producto").click()
    expect(d.locator(".field.invalid")).not_to_have_count(0)
    step("El formulario muestra errores por campo antes de enviar")
    sku = f"TA-{TAG}"
    d.locator("input[name=sku]").fill(sku)
    d.locator("input[name=name]").fill(f"Tomate de árbol (prueba {TAG})")
    d.locator("select[name=category_id]").select_option(label=f"Frutas (prueba {TAG})")
    d.locator("select[name=base_unit_id]").select_option(label="Kilogramo (kg)")
    d.locator("input[name=min_stock]").fill("20")
    d.get_by_role("button", name="Crear producto").click()
    det = dialog(page)
    expect(det.get_by_role("heading", name="Agregar presentación")).to_be_visible()
    det.locator("select[name=unit_id]").select_option(label="Bulto")
    det.locator("input[name=name]").last.fill("Bulto 50 kg")
    det.locator("input[name=factor_to_base]").fill("50")
    det.get_by_role("button", name="Agregar").click()
    expect(dialog(page).get_by_text("Bulto 50 kg")).to_be_visible()
    page.keyboard.press("Escape")
    step("Producto creado con presentación 'Bulto 50 kg' = 50 kg")

    # Compra
    page.goto(BASE + "/#/compras")
    page.get_by_role("button", name="+ Nueva compra").click()
    d = dialog(page)
    d.locator("select[name=supplier_id]").select_option(label=f"Proveedor de prueba {TAG}")
    d.locator("input[name=invoice_number]").fill(f"PRUEBA-{TAG}")
    d.locator("input[name=freight]").fill("10.000")
    d.locator("input[type=search]").fill(sku)
    d.locator(".search-results button").first.click()
    row = d.locator("tbody tr").last
    row.locator("select").select_option(label="Bulto 50 kg (= 50 kg)")
    row.get_by_label("Cantidad").fill("2")
    row.get_by_label("Precio unitario").fill("100.000")
    row.get_by_label("Merma recepción").fill("5")
    expect(d.get_by_text("Calculado por el servidor")).to_be_visible(timeout=8000)
    expect(d.locator(".kpi", has_text="Total").last).to_contain_text("210.000")
    # (2 × 100.000 + 10.000) / (100 kg − 5 kg merma) = 2.210,53 por kg
    expect(d.get_by_text(re.compile(r"2\.210,53"))).to_be_visible()
    step("Previsualización del servidor: total $210.000 y costo efectivo $2.210,53/kg")
    d.get_by_role("button", name="Guardar como borrador").click()
    det = dialog(page)
    expect(det.get_by_text("Borrador", exact=True)).to_be_visible()
    det.get_by_role("button", name="Confirmar compra").click()
    dialog(page).get_by_role("button", name="Confirmar compra").click()
    expect(page.locator(".toast", has_text="Compra confirmada")).to_be_visible()
    step("Compra guardada como borrador y confirmada")

    # Precio inicial (manual, con justificación)
    page.goto(BASE + "/#/precios")
    page.get_by_role("tab", name="Cambiar precio manualmente").click()
    page.locator("main input[type=search]").fill(sku)
    page.locator(".search-results button").first.click()
    page.fill("input[name=new_price]", "3.000")
    page.fill("textarea[name=justification]", "Precio inicial de prueba")
    page.get_by_role("button", name="Cambiar precio").click()
    expect(page.locator(".toast", has_text="Precio actualizado")).to_be_visible()
    step("Precio fijado manualmente con justificación ($3.000/kg)")

    # Venta en el punto de venta
    page.goto(BASE + "/#/ventas")
    page.locator("main input[type=search]").fill(sku)
    page.locator(".search-results button").first.click()
    page.get_by_role("radio", name="kg", exact=True).click()
    weight = page.get_by_label("Peso o cantidad", exact=True)
    weight.fill("2,5")
    expect(page.locator(".scale-amount")).to_contain_text("7.500")
    weight.press("Enter")
    expect(page.locator(".pos .total")).to_contain_text("7.500", timeout=8000)
    charge = page.get_by_role("button", name=re.compile("^Cobrar"))
    charge.click()
    dialog(page).get_by_role("button", name="Registrar venta").click()
    expect(page.locator(".toast", has_text="registrada por")).to_be_visible()
    step("Venta de 2,5 kg registrada: total $7.500 calculado por el servidor")

    # Venta mayor que la existencia: el servidor la marca y la interfaz no deja cobrar
    page.locator("main input[type=search]").fill(sku)
    page.locator(".search-results button").first.click()
    page.get_by_role("radio", name="kg", exact=True).click()
    weight.fill("1000")
    expect(page.get_by_text("Supera la existencia disponible")).to_be_visible()
    weight.press("Enter")
    expect(page.get_by_text("Existencias insuficientes")).to_be_visible(timeout=8000)
    expect(charge).to_be_disabled()
    step("La interfaz bloquea una venta mayor que la existencia (según el servidor)")
    page.locator(".cart-line").get_by_role("button", name=re.compile("^Quitar")).click()

    # Merma
    page.goto(BASE + "/#/inventario")
    page.get_by_role("tab", name="Registrar movimiento").click()
    page.locator("main input[type=search]").fill(sku)
    page.locator(".search-results button").first.click()
    page.select_option("select[name=tipo]", "merma")
    page.fill("input[name=cantidad]", "1,5")
    page.fill("input[name=motivo]", "Fruta golpeada (prueba)")
    page.get_by_role("button", name="Registrar").click()
    dialog(page).get_by_role("button", name="Confirmar").click()
    expect(page.locator(".toast", has_text="Nuevo saldo: 91")).to_be_visible()   # 95 − 2,5 − 1,5
    step("Merma registrada; saldo 91 kg (95 útiles − 2,5 vendidos − 1,5 merma)")

    # Anular la venta
    page.goto(BASE + "/#/ventas")
    page.get_by_role("tab", name="Historial de ventas").click()
    page.locator("main tbody tr").first.get_by_role("button", name="Ver").click()
    dialog(page).get_by_role("button", name="Anular venta").click()
    cd = dialog(page)
    cd.locator("textarea").fill("Prueba de anulación")
    cd.get_by_role("button", name="Anular venta").click()
    expect(page.locator(".toast", has_text="Venta anulada")).to_be_visible()
    step("Venta anulada con motivo")

    # Movimientos: debe existir la anulación compensatoria
    page.goto(BASE + "/#/inventario")
    page.get_by_role("tab", name="Movimientos").click()
    expect(page.locator("main tbody").get_by_text("Anulación de venta").first).to_be_visible()
    step("Movimientos muestran la anulación compensatoria (no se borró nada)")

    # Sugerencia de precio y aprobación
    page.goto(BASE + "/#/precios")
    page.get_by_role("button", name="Generar sugerencias").click()
    page.wait_for_timeout(1500)
    if page.locator("dialog[open]").count():
        page.keyboard.press("Escape")
    row = page.locator("main tbody tr", has_text=f"prueba {TAG}").first
    expect(row).to_contain_text("3.200")  # 2.210,53 / 0,7 = 3.157,9 → redondeo a 3.200
    row.get_by_role("button", name="Revisar").click()
    d = dialog(page)
    d.locator("textarea[name=note]").fill("Aprobación de prueba")
    d.get_by_role("button", name="Aprobar").click()
    dialog(page).get_by_role("button", name="Aprobar").click()   # confirmación
    expect(page.locator(".toast", has_text="Precio actualizado")).to_be_visible()
    step("Sugerencia 3.000 → 3.200 (2.210,53 / 0,7 = 3.157,89, redondeo a 50) revisada y aprobada")
    page.get_by_role("tab", name="Historial de precios").click()
    expect(page.locator("main tbody tr").first).to_contain_text("Aprobación de prueba")
    step("El historial registra precio anterior, nuevo, justificación y usuario")

    # Reporte y exportación
    page.goto(BASE + "/#/reportes")
    page.get_by_role("combobox", name="Reporte").select_option(label="Existencias actuales")
    page.get_by_role("button", name="Generar").click()
    expect(page.locator("main table").get_by_text(f"Tomate de árbol (prueba {TAG})")).to_be_visible()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Excel").click()
    step(f"Reporte de existencias generado y exportado a Excel ({dl.value.suggested_filename})")
    page.screenshot(path=str(OUT / "flujo_reporte.png"))

    page.goto(BASE + "/#/inicio")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "flujo_inicio.png"), full_page=True)
    browser.close()

print("\nPROBLEMAS:" if problems else "\nSin errores de página ni respuestas 5xx")
for x in problems:
    print(" -", x)
