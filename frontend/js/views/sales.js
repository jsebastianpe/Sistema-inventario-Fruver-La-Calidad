import { api, newIdempotencyKey } from "../api.js";
import { can, session } from "../app.js";
import { dateTime, money, num, parseDecimal, pct, todayISO } from "../format.js";
import {
  DataTable, badge, clear, confirmDialog, errorText, h, loadingEl, modal, showError, simpleTable, submitting, tabs, toast,
} from "../ui.js";
import { pageHeader, presentationOptions, productPicker } from "./common.js";

export async function render(el) {
  const content = h("div");
  const items = [];
  if (can("ventas.registrar")) items.push({ id: "pos", label: "Registrar venta" });
  items.push({ id: "historial", label: "Historial de ventas" });
  const show = (id) => { clear(content); (id === "pos" ? pos : history)(content); };
  el.append(pageHeader("Ventas"), tabs(items, show), content);
  show(items[0].id);
}

function pos(el) {
  const cart = [];  // { product, unitSel, qtyInput, discInput, row }
  const body = h("tbody");
  const summary = h("div", {}, h("p", { class: "muted" }, "Agregue productos."));
  const totalEl = h("div", { class: "total" }, money(0));
  const payment = h("select", { "aria-label": "Medio de pago" }, [["efectivo", "Efectivo"], ["transferencia", "Transferencia"], ["tarjeta", "Tarjeta"], ["otro", "Otro"]].map(([v, l]) => h("option", { value: v }, l)));
  const customer = h("input", { placeholder: "Cliente (opcional)", maxlength: 120, "aria-label": "Cliente" });
  const reference = h("input", { placeholder: "Referencia (opcional)", maxlength: 60, "aria-label": "Referencia" });
  const confirmBtn = h("button", { class: "btn primary", type: "button", disabled: true }, "Registrar venta");
  let key = newIdempotencyKey();
  let lastPreview = null;

  const add = (p) => {
    if (!p.current_price) { showError({ message: `"${p.name}" no tiene precio de venta aprobado` }); return; }
    const unitSel = h("select", { "aria-label": "Presentación" }, presentationOptions(p, "sale").map((o) => h("option", { value: o.value }, o.label)));
    const qtyInput = h("input", { inputmode: "decimal", value: "1", "aria-label": "Cantidad" });
    const discInput = h("input", { inputmode: "decimal", value: "0", "aria-label": "Descuento $" });
    const lineTotal = h("td", { class: "num", "data-label": "Total" }, "—");
    const row = h("tr", {}, h("td", { "data-label": "Producto" }, p.name), h("td", { "data-label": "Presentación" }, unitSel),
      h("td", { "data-label": "Cantidad" }, qtyInput), h("td", { "data-label": "Descuento $" }, discInput), lineTotal,
      h("td", { class: "actions" }, h("button", { class: "btn small ghost", type: "button", "aria-label": "Quitar", onclick: () => { cart.splice(cart.indexOf(item), 1); row.remove(); schedule(); } }, "✕")));
    const item = { product: p, unitSel, qtyInput, discInput, row, lineTotal };
    row.addEventListener("input", schedule);
    row.addEventListener("change", schedule);
    cart.push(item);
    body.append(row);
    qtyInput.select();
    schedule();
  };

  let timer;
  let previewedFor = null;  // carrito (serializado) al que corresponde la última previsualización
  const schedule = () => {
    // Un "change" al salir del campo no debe deshabilitar el botón si el carrito no cambió.
    if (cart.length && JSON.stringify(payload()) === previewedFor) return;
    confirmBtn.disabled = true; clearTimeout(timer); timer = setTimeout(refresh, 300);
  };
  const payload = () => ({
    payment_method: payment.value, customer_name: customer.value.trim() || null, reference: reference.value.trim() || null,
    lines: cart.map((c) => ({ product_id: c.product.id, product_unit_id: Number(c.unitSel.value),
      quantity: parseDecimal(c.qtyInput.value), discount: parseDecimal(c.discInput.value) ?? "0" })),
  });

  async function refresh() {
    lastPreview = null;
    previewedFor = null;
    if (!cart.length) { clear(summary, h("p", { class: "muted" }, "Agregue productos.")); totalEl.textContent = money(0); return; }
    const d = payload();
    if (d.lines.some((l) => !l.quantity || Number(l.quantity) <= 0)) { clear(summary, h("div", { class: "notice warn small" }, "Revise las cantidades.")); return; }
    try {
      const r = await api.post("/ventas/previsualizar", d);
      lastPreview = r;
      previewedFor = JSON.stringify(d);
      r.lines.forEach((l, i) => { cart[i].lineTotal.textContent = money(l.net_amount); });
      totalEl.textContent = money(r.total);
      const problems = r.lines.filter((l) => !l.stock_ok).map((l) => `${l.product_name}: disponible ${num(l.available)}`);
      clear(summary,
        simpleTable([{ title: "Concepto", key: "k" }, { title: "Valor", num: true, key: "v" }], [
          { k: "Bruto", v: money(r.gross_total) }, { k: "Descuentos", v: `${money(r.discount_total)} (${pct(r.discount_rate)})` },
          { k: "Impuestos incluidos", v: money(r.tax_total) }]),
        !r.discount_authorized ? h("div", { class: "notice danger small" }, `El descuento supera el máximo autorizado (${pct(r.max_discount_rate)}). Solicite autorización a un administrador.`) : null,
        problems.length ? h("div", { class: "notice danger small" }, "Existencias insuficientes: ", problems.join("; ")) : null);
      confirmBtn.disabled = !r.discount_authorized || problems.length > 0;
    } catch (e) { clear(summary, h("div", { class: "notice warn small" }, errorText(e))); }
  }

  confirmBtn.onclick = async () => {
    if (!lastPreview) return;
    const ok = await confirmDialog({ title: "Registrar venta", message: `Total a cobrar: ${money(lastPreview.total)} (${payment.selectedOptions[0].textContent}).`, confirmText: "Registrar" });
    if (!ok) return;
    try {
      const s = await submitting(confirmBtn, () => api.post("/ventas", payload(), { idempotencyKey: key }));
      toast(`Venta #${s.id} registrada por ${money(s.total)}`);
      key = newIdempotencyKey();
      cart.splice(0); clear(body); customer.value = ""; reference.value = ""; refresh();
    } catch (e) {
      showError(e);
      // Si fue un error de red, la venta pudo registrarse: reintentar con la MISMA clave no la duplica.
    }
  };

  el.append(h("div", { class: "pos" },
    h("div", { class: "card stack" }, h("h2", {}, "Productos"), productPicker({ onPick: add, showPrice: true }),
      h("div", { class: "table-wrap" }, h("table", { class: "responsive" }, h("thead", {}, h("tr", {}, ["Producto", "Presentación", "Cantidad", "Descuento $", "Total", ""].map((t) => h("th", {}, t)))), body))),
    h("div", { class: "card stack" }, h("h2", {}, "Total"), totalEl, summary, payment, customer, reference, confirmBtn,
      h("p", { class: "muted small" }, "Los precios los aplica el servidor según el precio vigente. Registro interno: no es factura electrónica."))));
}

function history(el) {
  const desde = h("input", { type: "date", name: "desde", value: todayISO(), "aria-label": "Desde" });
  const hasta = h("input", { type: "date", name: "hasta", value: todayISO(), "aria-label": "Hasta" });
  const estado = h("select", { name: "estado", "aria-label": "Estado" }, h("option", { value: "" }, "Todas"), h("option", { value: "confirmada" }, "Confirmadas"), h("option", { value: "anulada" }, "Anuladas"));
  const costs = can("costos.ver");
  const t = new DataTable({
    searchPlaceholder: "Buscar por cliente o referencia…", filters: [desde, hasta, estado],
    load: (p) => api.get("/ventas", p),
    columns: [
      { title: "N.º", render: (r) => `#${r.id}` }, { title: "Fecha", render: (r) => dateTime(r.sold_at) },
      { title: "Vendedor", key: "created_by_name" }, { title: "Pago", key: "payment_method" },
      { title: "Total", num: true, render: (r) => money(r.total) },
      ...(costs ? [{ title: "Margen bruto est.", num: true, render: (r) => money(r.gross_margin) }] : []),
      { title: "Estado", render: (r) => badge(r.status) },
      { title: "", actions: true, render: (r) => h("button", { class: "btn small", onclick: () => detail(r.id, t) }, "Ver") },
    ],
  });
  if (!can("ventas.anular")) el.append(h("p", { class: "muted small" }, `Se muestran las ventas registradas por ${session.me.usuario.full_name}.`));
  el.append(t.el);
  t.refresh();
}

async function detail(id, t) {
  const body = h("div", {}, loadingEl());
  const m = modal({ title: `Venta #${id}`, body, wide: true });
  const s = await api.get(`/ventas/${id}`);
  const costs = can("costos.ver");
  clear(body, h("div", { class: "stack" },
    h("div", { class: "row" }, badge(s.status), h("span", {}, dateTime(s.sold_at)), h("span", { class: "muted" }, s.created_by_name),
      s.customer_name ? h("span", {}, `Cliente: ${s.customer_name}`) : null),
    simpleTable([
      { title: "Producto", key: "product_name" }, { title: "Presentación", key: "unit_name" },
      { title: "Cantidad", num: true, render: (r) => num(r.quantity) }, { title: "Precio", num: true, render: (r) => money(r.unit_price) },
      { title: "Descuento", num: true, render: (r) => money(r.discount) }, { title: "Total", num: true, render: (r) => money(r.net_amount) },
      ...(costs ? [{ title: "Costo", num: true, render: (r) => money(r.cost_amount) }] : []),
    ], s.lines),
    h("div", { class: "grid kpis" },
      h("div", { class: "card kpi" }, h("div", { class: "label" }, "Total"), h("div", { class: "value" }, money(s.total))),
      h("div", { class: "card kpi" }, h("div", { class: "label" }, "Impuestos incluidos"), h("div", { class: "value" }, money(s.tax_total))),
      costs ? h("div", { class: "card kpi" }, h("div", { class: "label" }, "Margen bruto estimado"), h("div", { class: "value" }, money(s.gross_margin)), h("div", { class: "hint" }, "no es utilidad neta")) : null),
    h("p", { class: "muted small" }, "Precios y costos guardados al momento de la venta; no cambian si el precio del producto cambia después."),
    s.cancelled_at ? h("div", { class: "notice danger small" }, `Anulada ${dateTime(s.cancelled_at)}: ${s.cancel_reason}`) : null,
    s.status === "confirmada" && can("ventas.anular") ? h("div", { class: "row end" }, h("button", { class: "btn danger", onclick: async () => {
      const reason = await confirmDialog({ title: "Anular venta", danger: true, confirmText: "Anular venta",
        message: "La mercancía volverá al inventario con un movimiento compensatorio. La venta queda registrada como anulada.", input: { label: "Motivo", minLength: 3 } });
      if (!reason) return;
      try { await api.post(`/ventas/${id}/anular`, { reason }); toast("Venta anulada"); m.close(); t.refresh(); } catch (e) { showError(e); }
    } }, "Anular venta")) : null));
}
