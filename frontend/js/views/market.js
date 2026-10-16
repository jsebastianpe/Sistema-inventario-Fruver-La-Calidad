import { api } from "../api.js";
import { can } from "../app.js";
import { date, dateTime, money, num, signedPct } from "../format.js";
import {
  DataTable, applyServerErrors, badge, clear, field, h, lineChart, loadingEl, modal, readForm, showError,
  simpleTable, submitting, tabs, toast,
} from "../ui.js";
import { pageHeader, productPicker, units } from "./common.js";

export async function render(el) {
  const content = h("div");
  const items = [{ id: "estado", label: "Estado de la actualización" }, { id: "referencias", label: "Referencias por producto" },
    { id: "explorar", label: "Explorar cotizaciones" }, { id: "externos", label: "Productos de la fuente" }];
  const show = (id) => { clear(content); ({ estado: status, referencias: references, explorar: explore, externos: external })[id](content); };
  el.append(pageHeader("Precios de mercado"),
    h("div", { class: "notice info small" }, "Fuente: DANE – SIPSA, precios mayoristas promedio por ciudad. Son referencias de mercado: no reemplazan el costo real de compra ni son precios minoristas."),
    tabs(items, show), content);
  show("estado");
}

async function status(el) {
  el.append(loadingEl());
  const s = await api.get("/mercado/estado");
  const hb = s.proceso_automatico;
  clear(el,
    s.fuentes.map((f) => h("div", { class: "card" },
      h("div", { class: "row between" }, h("h2", {}, f.name),
        can("mercado.gestionar") ? h("button", { class: "btn primary", onclick: async (e) => {
          try { await submitting(e.currentTarget, () => api.post("/mercado/sincronizar", undefined, {})); toast("Actualización solicitada; se ejecutará en el próximo minuto."); } catch (err) { showError(err); }
        } }, "Actualizar ahora") : null),
      h("p", {}, "Estado: ", f.last_status ? badge(f.last_status) : h("span", { class: "badge" }, "sin ejecuciones"),
        f.sync_requested_at ? h("span", { class: "badge info" }, " actualización solicitada") : null),
      h("p", { class: "small" }, `Último intento: ${dateTime(f.last_attempt_at)} · Último éxito: ${dateTime(f.last_success_at)} · Datos hasta: ${date(f.latest_price_date)}`),
      f.last_error ? h("div", { class: "notice danger small" }, f.last_error, h("div", {}, "Se conservan las últimas cotizaciones válidas con su fecha original.")) : null)),
    h("div", { class: "card" }, h("h2", {}, "Proceso automático"),
      hb ? h("p", {}, hb.activo ? h("span", { class: "badge ok" }, "Activo") : h("span", { class: "badge danger" }, "Sin latido reciente"),
        ` Último latido: ${dateTime(hb.ultimo_latido)} · ${hb.info || ""}`) : h("p", { class: "muted" }, "El proceso automático aún no ha reportado actividad.")),
    h("div", { class: "card" }, h("h2", {}, "Últimas ejecuciones"), simpleTable([
      { title: "Inicio", render: (r) => dateTime(r.started_at) }, { title: "Estado", render: (r) => badge(r.status) },
      { title: "Origen", key: "triggered_by" }, { title: "Leídos", num: true, render: (r) => num(r.records_read) },
      { title: "Nuevos", num: true, render: (r) => num(r.records_inserted) }, { title: "Repetidos", num: true, render: (r) => num(r.records_duplicated) },
      { title: "Inválidos", num: true, render: (r) => num(r.records_invalid) }, { title: "Variaciones", num: true, render: (r) => num(r.anomalies) },
      { title: "Datos hasta", render: (r) => date(r.max_price_date) },
      { title: "Detalle", render: (r) => (r.error_message ? h("span", { class: "small" }, r.error_message.slice(0, 160)) : "") },
    ], s.ejecuciones, { emptyText: "Aún no hay ejecuciones" })));
}

async function references(el) {
  el.append(loadingEl());
  const refs = await api.get("/mercado/referencias");
  const costs = can("costos.ver");
  clear(el,
    can("mercado.gestionar") ? h("div", { class: "row end" }, h("button", { class: "btn primary", onclick: () => mappingForm(() => { clear(el); references(el); }) }, "+ Nueva correspondencia")) : null,
    h("p", { class: "muted small" }, "Una correspondencia indica qué producto de la fuente, en qué mercado, equivale a un producto del catálogo y con qué factor de conversión. Solo se usan referencias con unidad confirmada y no desactualizadas."),
    simpleTable([
      { title: "Producto", key: "product_name" }, { title: "Producto fuente", key: "market_product_name" }, { title: "Mercado", key: "market" },
      { title: "Fecha", render: (r) => date(r.price_date) },
      { title: "Precio publicado", num: true, render: (r) => h("span", {}, money(r.source_price), h("div", { class: "muted small" }, r.source_unit ? `por ${r.source_unit}` : "unidad por confirmar")) },
      { title: "Por unidad base", num: true, render: (r) => money(r.price_per_base_unit) },
      ...(costs ? [{ title: "Costo real", num: true, render: (r) => money(r.unit_cost) }, { title: "Costo vs. mercado", num: true, render: (r) => signedPct(r.cost_vs_market) }] : []),
      { title: "Calidad", render: (r) => (r.usable ? h("span", { class: "badge ok" }, "Utilizable") : h("span", { class: "badge warn" }, r.reason)) },
      { title: "", actions: true, render: (r) => h("button", { class: "btn small", onclick: () => chart(r.market_product_id, r.market, r.market_product_name) }, "Gráfica") },
    ], refs, { emptyText: "No hay correspondencias. Cree una para comparar costos con el mercado." }));
}

async function mappingForm(done) {
  let product = null;
  const ext = await api.get("/mercado/productos-externos");
  const markets = await api.get("/mercado/mercados");
  const chosen = h("div", { class: "notice info small" }, "Seleccione el producto del catálogo.");
  const form = h("form", { class: "form", novalidate: true },
    productPicker({ onPick: (p) => { product = p; chosen.textContent = `Producto: ${p.name} (unidad base ${p.base_unit_code})`; } }), chosen,
    h("div", { class: "cols" },
      field({ name: "market_product_id", label: "Producto de la fuente", type: "select", required: true,
        options: [{ value: "", label: "Seleccione…" }, ...ext.map((e) => ({ value: e.id, label: `${e.name}${e.price_unit_code ? "" : " (unidad sin confirmar)"}` }))] }),
      field({ name: "market", label: "Mercado (ciudad)", type: "select", required: true, options: [{ value: "", label: "Seleccione…" }, ...markets.map((m) => ({ value: m, label: m }))] }),
      field({ name: "conversion_factor", label: "Factor de conversión", type: "decimal", required: true, value: "1",
        hint: "Precio por unidad base = precio publicado × factor. Si ambos están en kg, use 1." })),
    field({ name: "notes", label: "Notas (variedad, calidad, presentación)", type: "textarea" }),
    h("div", { class: "notice warn small" }, "No asuma equivalencia solo por el nombre: verifique variedad, calidad y presentación. Productos con * en SIPSA agrupan varias variedades."));
  const save = h("button", { class: "btn primary", type: "submit" }, "Guardar correspondencia");
  form.append(h("div", { class: "row end" }, save));
  const m = modal({ title: "Nueva correspondencia", body: form, wide: true });
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!product) { showError({ message: "Seleccione el producto del catálogo" }); return; }
    const d = readForm(form);
    if (!d) return;
    try {
      await submitting(save, () => api.post("/mercado/correspondencias", { ...d, product_id: product.id, market_product_id: Number(d.market_product_id) }));
      toast("Correspondencia creada"); m.close(); done();
    } catch (err) { applyServerErrors(form, err); }
  };
}

async function chart(mpId, market, name) {
  const body = h("div", {}, loadingEl());
  modal({ title: `${name} — ${market}`, body, wide: true });
  const r = await api.get("/mercado/cotizaciones", { market_product_id: mpId, mercado: market, page_size: 200 });
  const pts = [...r.items].reverse().map((q) => ({ x: q.price_date, y: Number(q.price), alert: q.flag !== "normal" }));
  clear(body, lineChart(pts, { formatY: (v) => money(Math.round(v)) }),
    h("p", { class: "muted small" }, "Los puntos rojos marcan variaciones extraordinarias frente a la cotización anterior."),
    simpleTable([{ title: "Fecha", render: (q) => date(q.price_date) }, { title: "Precio", num: true, render: (q) => money(q.price) },
      { title: "Variación", num: true, render: (q) => signedPct(q.variation_rate) },
      { title: "Publicado", render: (q) => dateTime(q.published_at) }], r.items.slice(0, 30)));
}

async function explore(el) {
  const [ext, markets] = await Promise.all([api.get("/mercado/productos-externos"), api.get("/mercado/mercados")]);
  const mp = h("select", { name: "market_product_id", "aria-label": "Producto" }, h("option", { value: "" }, "Todos los productos"), ext.map((e) => h("option", { value: e.id }, e.name)));
  const mk = h("select", { name: "mercado", "aria-label": "Mercado" }, h("option", { value: "" }, "Todos los mercados"), markets.map((m) => h("option", { value: m }, m)));
  const only = h("label", { class: "check small" }, h("input", { type: "checkbox", name: "solo_variaciones" }), "Solo variaciones extraordinarias");
  const names = Object.fromEntries(ext.map((e) => [e.id, e]));
  const t = new DataTable({
    search: false, filters: [mp, mk, h("input", { type: "date", name: "desde", "aria-label": "Desde" }), h("input", { type: "date", name: "hasta", "aria-label": "Hasta" }), only],
    load: (p) => api.get("/mercado/cotizaciones", p),
    columns: [
      { title: "Fecha", render: (q) => date(q.price_date) }, { title: "Producto", render: (q) => names[q.market_product_id]?.name },
      { title: "Mercado", key: "market" }, { title: "Precio", num: true, render: (q) => money(q.price) },
      { title: "Unidad", render: (q) => names[q.market_product_id]?.price_unit_code || h("span", { class: "badge warn" }, "por confirmar") },
      { title: "Variación", num: true, render: (q) => h("span", {}, signedPct(q.variation_rate), q.flag !== "normal" ? h("span", { class: "badge danger" }, " extraordinaria") : null) },
    ],
  });
  el.append(t.el);
  t.refresh();
}

async function external(el) {
  el.append(loadingEl());
  const [ext, us] = await Promise.all([api.get("/mercado/productos-externos"), units()]);
  clear(el,
    h("div", { class: "notice warn small" }, "La documentación oficial del SIPSA no indica la unidad del precio promedio por ciudad. Confirme la unidad de cada producto (p. ej. contrastando con los boletines del DANE) antes de usarlo para comparar costos."),
    simpleTable([
      { title: "Código", key: "external_code" }, { title: "Producto (fuente)", key: "name" },
      { title: "Mercados", num: true, key: "markets" }, { title: "Último dato", render: (r) => date(r.latest_price_date) },
      { title: "Unidad del precio", render: (r) => (r.price_unit_code ? h("span", { class: "badge ok" }, r.price_unit_code) : h("span", { class: "badge warn" }, "por confirmar")) },
      { title: "", actions: true, render: (r) => (can("mercado.gestionar") ? unitControl(r, us) : null) },
    ], ext, { emptyText: "Aún no se han descargado datos de la fuente." }));
}

function unitControl(r, us) {
  const sel = h("select", { "aria-label": "Unidad" }, h("option", { value: "" }, "Unidad…"), us.map((u) => h("option", { value: u.code, selected: u.code === r.price_unit_code }, u.name)));
  return h("span", { class: "row" }, sel, h("button", { class: "btn small", onclick: async () => {
    if (!sel.value) return;
    try { await api.patch(`/mercado/productos-externos/${r.id}`, { price_unit_code: sel.value }); toast(`Unidad confirmada para ${r.name}`); } catch (e) { showError(e); }
  } }, "Confirmar"));
}
