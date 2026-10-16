import { api, newIdempotencyKey } from "../api.js";
import { can } from "../app.js";
import { date, dateTime, money, num, parseDecimal, todayISO } from "../format.js";
import {
  DataTable, applyServerErrors, badge, clear, confirmDialog, errorText, field, h, loadingEl, modal, readForm,
  showError, simpleTable, submitting, toast,
} from "../ui.js";
import { opt, pageHeader, presentationOptions, productPicker, suppliers } from "./common.js";

let table;

export async function render(el) {
  const sups = await suppliers();
  const filters = [
    h("select", { name: "proveedor_id", "aria-label": "Proveedor" }, opt(sups, "name", "Todos los proveedores").map((o) => h("option", { value: o.value }, o.label))),
    h("select", { name: "estado", "aria-label": "Estado" }, h("option", { value: "" }, "Todos los estados"),
      ["borrador", "confirmada", "anulada"].map((s) => h("option", { value: s }, s))),
    h("input", { type: "date", name: "desde", "aria-label": "Desde" }), h("input", { type: "date", name: "hasta", "aria-label": "Hasta" }),
  ];
  table = new DataTable({
    search: false, filters, load: (p) => api.get("/compras", p),
    columns: [
      { title: "N.º", render: (r) => `#${r.id}` }, { title: "Fecha", render: (r) => date(r.purchase_date) },
      { title: "Proveedor", key: "supplier_name" }, { title: "Factura", render: (r) => r.invoice_number || "—" },
      { title: "Líneas", num: true, render: (r) => r.lines.length }, { title: "Total", num: true, render: (r) => money(r.total) },
      { title: "Estado", render: (r) => badge(r.status) },
      { title: "", actions: true, render: (r) => h("button", { class: "btn small", onclick: () => detail(r.id) }, "Ver") },
    ],
  });
  el.append(pageHeader("Compras", can("compras.registrar") ? h("button", { class: "btn primary", onclick: () => editor() }, "+ Nueva compra") : null), table.el);
  await table.refresh();
}

async function detail(id) {
  const body = h("div", {}, loadingEl());
  const m = modal({ title: `Compra #${id}`, body, wide: true });
  const p = await api.get(`/compras/${id}`);
  const actions = [];
  if (p.status === "borrador" && can("compras.registrar")) actions.push(h("button", { class: "btn", onclick: () => { m.close(); editor(p); } }, "Editar borrador"));
  if (p.status === "borrador" && can("compras.confirmar")) actions.push(h("button", { class: "btn primary", onclick: async () => {
    const ok = await confirmDialog({ title: "Confirmar compra", message: `Se agregarán al inventario ${p.lines.length} producto(s) y se actualizará su costo promedio. Total ${money(p.total)}. Esta acción no se puede editar después; solo anular.`, confirmText: "Confirmar compra" });
    if (!ok) return;
    try { await api.post(`/compras/${id}/confirmar`); toast("Compra confirmada: inventario y costos actualizados"); m.close(); table.refresh(); } catch (e) { showError(e); }
  } }, "Confirmar compra"));
  if (p.status !== "anulada" && can("compras.anular")) actions.push(h("button", { class: "btn danger", onclick: async () => {
    const reason = await confirmDialog({ title: "Anular compra", danger: true, confirmText: "Anular",
      message: p.status === "confirmada" ? "Se retirará del inventario la mercancía de esta compra con movimientos compensatorios. Si parte ya se vendió, la anulación será rechazada." : "El borrador quedará anulado.",
      input: { label: "Motivo de la anulación", minLength: 3 } });
    if (!reason) return;
    try { await api.post(`/compras/${id}/anular`, { reason }); toast("Compra anulada"); m.close(); table.refresh(); } catch (e) { showError(e); }
  } }, "Anular"));
  clear(body, h("div", { class: "stack" },
    h("div", { class: "row" }, badge(p.status), h("span", {}, p.supplier_name), h("span", { class: "muted" }, `Fecha ${date(p.purchase_date)}`),
      p.invoice_number ? h("span", { class: "muted" }, `Factura ${p.invoice_number}`) : null),
    linesTable(p.lines),
    totalsBox(p),
    p.notes ? h("p", {}, p.notes) : null,
    p.confirmed_at ? h("p", { class: "muted small" }, `Confirmada ${dateTime(p.confirmed_at)}`) : null,
    p.cancelled_at ? h("div", { class: "notice danger small" }, `Anulada ${dateTime(p.cancelled_at)}: ${p.cancel_reason}`) : null,
    h("div", { class: "row end" }, actions)));
}

const linesTable = (lines) => simpleTable([
  { title: "Producto", render: (r) => h("span", {}, r.product_name, h("div", { class: "muted small" }, r.unit_name)) },
  { title: "Cant.", num: true, render: (r) => num(r.quantity) }, { title: "Precio unit.", num: true, render: (r) => money(r.unit_price) },
  { title: "Neto", num: true, render: (r) => money(r.net_amount) }, { title: "Reparto", num: true, render: (r) => money(r.allocated_extra) },
  { title: "Impuesto", num: true, render: (r) => money(r.tax_amount) },
  { title: "Útil (base)", num: true, render: (r) => num(r.usable_qty) },
  { title: "Costo efectivo / base", num: true, render: (r) => money(r.unit_cost_base) },
], lines);

const totalsBox = (p) => h("div", { class: "grid kpis" },
  [["Subtotal", p.subtotal], ["Impuestos", p.tax_total], ["Transporte", p.freight], ["Otros costos", p.other_costs], ["Descuento", p.discount], ["Total", p.total]]
    .filter(([, v]) => v !== undefined).map(([l, v]) => h("div", { class: "card kpi" }, h("div", { class: "label" }, l), h("div", { class: "value" }, money(v)))));

async function editor(existing = null) {
  const sups = await suppliers();
  const lines = [];   // { product, row }
  const linesBody = h("tbody");
  const preview = h("div", { class: "muted" }, "Agregue productos para ver los totales calculados.");
  const header = h("form", { class: "form", novalidate: true },
    h("div", { class: "cols" },
      field({ name: "supplier_id", label: "Proveedor", type: "select", required: true, value: existing?.supplier_id, options: opt(sups.filter((s) => s.is_active), "name", "Seleccione…") }),
      field({ name: "purchase_date", label: "Fecha", type: "date", required: true, value: existing?.purchase_date || todayISO() }),
      field({ name: "invoice_number", label: "N.º factura / documento", value: existing?.invoice_number }),
      field({ name: "allocation_method", label: "Repartir transporte y otros por", type: "select", value: existing?.allocation_method || "valor",
        options: [{ value: "valor", label: "Valor de cada línea" }, { value: "peso", label: "Peso (kg) de cada línea" }] })),
    h("div", { class: "cols" },
      field({ name: "freight", label: "Transporte", type: "money", value: existing?.freight ?? "0" }),
      field({ name: "other_costs", label: "Otros costos atribuibles", type: "money", value: existing?.other_costs ?? "0" }),
      field({ name: "discount", label: "Descuento global", type: "money", value: existing?.discount ?? "0" })),
    field({ name: "notes", label: "Observaciones", type: "textarea", value: existing?.notes }));

  const addLine = (product, init = {}) => {
    const sel = h("select", { "aria-label": "Presentación" }, presentationOptions(product, "purchase").map((o) => h("option", { value: o.value, selected: String(o.value) === String(init.product_unit_id) }, o.label)));
    const inp = (v, label) => h("input", { inputmode: "decimal", value: v ?? "", "aria-label": label });
    const q = inp(init.quantity, "Cantidad"), price = inp(init.unit_price, "Precio unitario"), disc = inp(init.line_discount ?? "0", "Descuento"),
      tax = inp(init.tax_rate ?? product.tax_rate, "Impuesto"), waste = inp(init.waste_qty ?? "0", "Merma recepción"),
      exp = h("input", { type: "date", value: init.expiry_date ?? "", "aria-label": "Vence" });
    const row = h("tr", {},
      h("td", { "data-label": "Producto" }, product.name), h("td", { "data-label": "Presentación" }, sel),
      h("td", { "data-label": "Cantidad" }, q), h("td", { "data-label": "Precio unit." }, price), h("td", { "data-label": "Desc. línea" }, disc),
      h("td", { "data-label": "Impuesto" }, tax), h("td", { "data-label": `Merma (${product.base_unit_code})` }, waste), h("td", { "data-label": "Vence" }, exp),
      h("td", { class: "actions" }, h("button", { class: "btn small ghost", type: "button", "aria-label": "Quitar línea", onclick: () => {
        lines.splice(lines.findIndex((l) => l.row === row), 1); row.remove(); schedule();
      } }, "✕")));
    const line = { product, row, read: () => ({ product_id: product.id, product_unit_id: Number(sel.value), quantity: parseDecimal(q.value),
      unit_price: parseDecimal(price.value), line_discount: parseDecimal(disc.value) ?? "0", tax_rate: parseDecimal(tax.value) ?? "0",
      waste_qty: parseDecimal(waste.value) ?? "0", expiry_date: exp.value || null }) };
    row.addEventListener("input", schedule);
    row.addEventListener("change", schedule);
    lines.push(line);
    linesBody.append(row);
    schedule();
  };

  let timer;
  function schedule() { clearTimeout(timer); timer = setTimeout(runPreview, 400); }
  function payload() {
    const d = readForm(header);
    if (!d) return null;
    return { ...d, supplier_id: Number(d.supplier_id), freight: d.freight ?? "0", other_costs: d.other_costs ?? "0", discount: d.discount ?? "0",
      lines: lines.map((l) => l.read()) };
  }
  async function runPreview() {
    const d = payload();
    if (!d || !d.lines.length || d.lines.some((l) => !l.quantity || l.unit_price === null)) { clear(preview, "Complete cantidad y precio de cada línea para ver los totales."); return; }
    try {
      const r = await api.post("/compras/previsualizar", d);
      clear(preview, h("p", { class: "muted small" }, "Calculado por el servidor con las reglas vigentes (no se ha guardado):"),
        linesTable(r.lines), totalsBox({ subtotal: r.subtotal, tax_total: r.tax_total, total: r.total }));
    } catch (e) { clear(preview, h("div", { class: "notice warn small" }, errorText(e))); }
  }
  header.addEventListener("change", schedule);

  const picker = productPicker({ onPick: (p) => addLine(p) });
  const save = h("button", { class: "btn primary", type: "button" }, existing ? "Guardar borrador" : "Guardar como borrador");
  const key = newIdempotencyKey();
  save.onclick = async () => {
    const d = payload();
    if (!d) return;
    if (!d.lines.length) { showError({ message: "Agregue al menos un producto" }); return; }
    try {
      const p = await submitting(save, () => (existing ? api.put(`/compras/${existing.id}`, d) : api.post("/compras", d, { idempotencyKey: key })));
      toast(`Compra #${p.id} guardada como borrador. Las existencias no cambian hasta confirmarla.`);
      m.close(); table.refresh(); detail(p.id);
    } catch (err) { applyServerErrors(header, err); }
  };
  const m = modal({ title: existing ? `Editar compra #${existing.id}` : "Nueva compra", wide: true, body: h("div", { class: "stack" },
    header, h("h3", {}, "Productos"), picker,
    h("div", { class: "table-wrap" }, h("table", { class: "responsive" }, h("thead", {}, h("tr", {},
      ["Producto", "Presentación", "Cantidad", "Precio unit. (sin imp.)", "Desc. línea", "Impuesto (0-0,99)", "Merma recepción (base)", "Vence", ""].map((t) => h("th", {}, t)))), linesBody)),
    h("div", { class: "card" }, h("h3", {}, "Totales y costo efectivo"), preview)),
  actions: [save] });
  for (const l of existing?.lines || []) {
    const prod = await api.get(`/productos/${l.product_id}`);
    addLine(prod, l);
  }
}
