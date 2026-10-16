import { api, download } from "../api.js";
import { can } from "../app.js";
import { date, monthStartISO, money, num, pct, qty, todayISO } from "../format.js";
import { clear, h, loadingEl, showError, simpleTable } from "../ui.js";
import { categories, opt, pageHeader, productPicker, suppliers } from "./common.js";

const NAMES = {
  "existencias": "Existencias actuales", "movimientos": "Movimientos por producto", "rotacion": "Productos de mayor rotación",
  "mermas": "Mermas registradas", "compras-por-proveedor": "Compras por proveedor", "ventas-por-periodo": "Ventas por período",
  "margen-bruto": "Margen bruto estimado", "evolucion-costos": "Evolución de costos", "evolucion-mercado": "Evolución de precios de mercado",
  "historial-precios": "Historial de precios de venta", "costo-vs-mercado": "Costos reales vs. mercado", "auditoria": "Auditoría",
};
const FORMAT = { dinero: money, cantidad: (v) => qty(v), numero: num, porcentaje: pct, fecha: date, texto: (v) => v };

export async function render(el) {
  const [available, cats, sups] = await Promise.all([api.get("/reportes"), categories(), suppliers()]);
  const sel = h("select", { "aria-label": "Reporte" }, available.map((r) => h("option", { value: r.nombre }, NAMES[r.nombre] || r.nombre)));
  const desde = h("input", { type: "date", value: monthStartISO(), "aria-label": "Desde" });
  const hasta = h("input", { type: "date", value: todayISO(), "aria-label": "Hasta" });
  const cat = h("select", { "aria-label": "Categoría" }, opt(cats, "name", "Todas las categorías").map((o) => h("option", { value: o.value }, o.label)));
  const sup = h("select", { "aria-label": "Proveedor" }, opt(sups, "name", "Todos los proveedores").map((o) => h("option", { value: o.value }, o.label)));
  const mercado = h("input", { placeholder: "Mercado (p. ej. BOGOTÁ, D.C.)", "aria-label": "Mercado" });
  let product = null;
  const prodLabel = h("span", { class: "muted small" }, "Todos los productos");
  const picker = productPicker({ activeOnly: false, onPick: (p) => { product = p; prodLabel.textContent = `Producto: ${p.name}`; } });
  const out = h("div");
  const params = () => ({ desde: desde.value, hasta: hasta.value, product_id: product?.id, category_id: cat.value, supplier_id: sup.value, mercado: mercado.value.trim() || undefined });
  const run = async () => {
    clear(out, loadingEl());
    try { clear(out, renderReport(await api.get(`/reportes/${sel.value}`, params()))); } catch (e) { clear(out, h("div", { class: "notice warn" }, e.message)); }
  };
  const exportBtns = can("reportes.exportar") ? [
    h("button", { class: "btn", onclick: () => download(`/reportes/${sel.value}`, { ...params(), formato: "xlsx" }).catch(showError) }, "Excel"),
    h("button", { class: "btn", onclick: () => download(`/reportes/${sel.value}`, { ...params(), formato: "csv" }).catch(showError) }, "CSV")] : [];
  el.append(pageHeader("Reportes"),
    h("div", { class: "card stack" },
      h("div", { class: "toolbar" }, h("div", { class: "grow" }, sel), desde, hasta, cat, sup, mercado),
      h("div", { class: "toolbar" }, h("div", { class: "grow" }, picker), prodLabel,
        h("button", { class: "btn small ghost", onclick: () => { product = null; prodLabel.textContent = "Todos los productos"; } }, "Quitar producto")),
      h("div", { class: "row end" }, exportBtns, h("button", { class: "btn primary", onclick: run }, "Generar"))),
    out);
}

function renderReport(r) {
  const cols = r.columnas.map((c) => ({ title: c.title, num: ["dinero", "cantidad", "numero", "porcentaje"].includes(c.kind),
    render: (row) => (row[c.key] === null || row[c.key] === undefined || row[c.key] === "" ? "—" : FORMAT[c.kind](row[c.key])) }));
  const totals = Object.keys(r.totales || {}).length ? h("tr", {}, r.columnas.map((c, i) => {
    const v = r.totales[c.key];
    return h("td", { class: cols[i].num ? "num" : "" }, i === 0 ? "Totales" : v === undefined ? "" : FORMAT[c.kind](v));
  })) : null;
  const extra = Object.entries(r.totales || {}).filter(([k]) => !r.columnas.some((c) => c.key === k));
  return h("div", { class: "card stack" }, h("h2", {}, r.titulo),
    r.notas.map((n) => h("div", { class: "notice info small" }, n)),
    extra.length ? h("p", {}, extra.map(([k, v]) => `${k.replaceAll("_", " ")}: ${money(v)}`).join(" · ")) : null,
    r.columnas.length ? simpleTable(cols, r.filas, { emptyText: "Sin datos para los filtros seleccionados", footer: totals }) : null,
    h("p", { class: "muted small" }, `${r.filas.length} fila(s).`));
}
