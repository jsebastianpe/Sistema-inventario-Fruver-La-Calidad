import { api, newIdempotencyKey } from "../api.js";
import { can } from "../app.js";
import { MOVEMENT_LABELS, date, dateTime, money, num, qty } from "../format.js";
import {
  DataTable, applyServerErrors, clear, confirmDialog, field, h, readForm, showError, submitting, tabs, toast,
} from "../ui.js";
import { pageHeader, presentationOptions, productPicker } from "./common.js";

export async function render(el) {
  const content = h("div");
  const items = [{ id: "existencias", label: "Existencias" }, { id: "movimientos", label: "Movimientos" }];
  if (can("inventario.mermas") || can("inventario.ajustar")) items.push({ id: "registrar", label: "Registrar movimiento" });
  items.push({ id: "lotes", label: "Lotes y vencimientos" });
  const show = (id) => { clear(content); ({ existencias: stock, movimientos: movements, registrar: register, lotes: lots })[id](content); };
  el.append(pageHeader("Inventario"), tabs(items, show), content);
  show("existencias");
}

function stock(el) {
  const costs = can("costos.ver");
  const t = new DataTable({
    filters: [h("label", { class: "check small" }, h("input", { type: "checkbox", name: "bajo_minimo" }), "Solo bajo mínimo")],
    load: (p) => api.get("/productos", { ...p, activo: true }),
    columns: [
      { title: "Producto", sort: "name", render: (r) => h("span", {}, h("strong", {}, r.name), h("div", { class: "muted small" }, r.category_name)) },
      { title: "Existencia", num: true, sort: "-stock", sortDesc: "stock", render: (r) => qty(r.stock, r.base_unit_code) },
      { title: "Mínimo", num: true, render: (r) => qty(r.min_stock, r.base_unit_code) },
      { title: "Estado", render: (r) => (r.low_stock ? h("span", { class: "badge warn" }, "Bajo mínimo") : h("span", { class: "badge ok" }, "OK")) },
      ...(costs ? [{ title: "Costo promedio", num: true, render: (r) => money(r.avg_cost) }, { title: "Valor estimado", num: true, render: (r) => money(r.stock_value) }] : []),
    ],
  });
  el.append(costs ? h("p", { class: "muted small" }, "Valor estimado = existencia × costo promedio ponderado.") : null, t.el);
  t.refresh();
}

function movements(el) {
  const tipo = h("select", { name: "tipo", "aria-label": "Tipo" }, h("option", { value: "" }, "Todos los tipos"),
    Object.entries(MOVEMENT_LABELS).map(([v, l]) => h("option", { value: v }, l)));
  const desde = h("input", { type: "date", name: "desde", "aria-label": "Desde" });
  const hasta = h("input", { type: "date", name: "hasta", "aria-label": "Hasta" });
  const costs = can("costos.ver");
  let productId = null;
  const chosen = h("span", { class: "muted small" }, "Todos los productos");
  const picker = productPicker({ activeOnly: false, onPick: (p) => { productId = p.id; chosen.textContent = `Producto: ${p.name}`; t.refresh(1); } });
  const reset = h("button", { class: "btn small ghost", type: "button", onclick: () => { productId = null; chosen.textContent = "Todos los productos"; t.refresh(1); } }, "Quitar filtro");
  const t = new DataTable({
    search: false, filters: [tipo, desde, hasta],
    load: (p) => api.get("/inventario/movimientos", { ...p, product_id: productId }),
    columns: [
      { title: "Fecha", render: (r) => dateTime(r.created_at) },
      { title: "Producto", key: "product_name" },
      { title: "Tipo", render: (r) => h("span", {}, MOVEMENT_LABELS[r.movement_type] || r.movement_type, r.reverses_movement_id ? h("div", { class: "muted small" }, `revierte #${r.reverses_movement_id}`) : null) },
      { title: "Cantidad (base)", num: true, render: (r) => h("span", { class: Number(r.quantity_base) < 0 ? "" : "" }, `${Number(r.quantity_base) > 0 ? "+" : ""}${num(r.quantity_base)}`) },
      { title: "Saldo", num: true, render: (r) => num(r.balance_after) },
      ...(costs ? [{ title: "Costo unit.", num: true, render: (r) => money(r.unit_cost) }] : []),
      { title: "Documento / motivo", render: (r) => h("span", {}, r.reference_type && r.reference_type !== "manual" ? `${r.reference_type} #${r.reference_id}` : "", r.reason ? h("div", { class: "small" }, r.reason) : null) },
      { title: "Usuario", key: "user_name" },
      { title: "", actions: true, render: (r) => (can("inventario.ajustar") && r.reference_type === "manual" && !r.reverses_movement_id
        ? h("button", { class: "btn small ghost", onclick: () => reverse(r, t) }, "Revertir") : null) },
    ],
  });
  el.append(h("div", { class: "toolbar" }, h("div", { class: "grow" }, picker), chosen, reset), t.el);
  t.refresh();
}

async function reverse(mv, t) {
  const reason = await confirmDialog({
    title: `Revertir movimiento #${mv.id}`,
    message: `Se registrará un movimiento compensatorio de ${num(Math.abs(mv.quantity_base))} sobre "${mv.product_name}". El movimiento original no se borra.`,
    confirmText: "Registrar reversión", danger: true, input: { label: "Motivo de la reversión", minLength: 3 },
  });
  if (!reason) return;
  try { await api.post(`/inventario/movimientos/${mv.id}/revertir`, { motivo: reason }); toast("Reversión registrada"); t.refresh(); } catch (e) { showError(e); }
}

function register(el) {
  const types = [];
  if (can("inventario.mermas")) types.push({ value: "merma", label: "Merma / deterioro" });
  if (can("inventario.ajustar")) types.push(
    { value: "ajuste_entrada", label: "Ajuste de entrada" }, { value: "ajuste_salida", label: "Ajuste de salida" },
    { value: "devolucion_cliente", label: "Devolución de cliente" }, { value: "devolucion_proveedor", label: "Devolución a proveedor" },
    { value: "inicial", label: "Existencia inicial (solo productos sin movimientos)" });
  let product = null;
  const productInfo = h("div", { class: "notice info" }, "Seleccione un producto.");
  const dynamic = h("div", { class: "cols" });
  const form = h("form", { class: "form", novalidate: true },
    productPicker({ onPick: (p) => { product = p; renderDynamic(); } }), productInfo,
    h("div", { class: "cols" }, field({ name: "tipo", label: "Tipo de movimiento", type: "select", required: true, options: types })),
    dynamic,
    field({ name: "motivo", label: "Motivo", hint: "Obligatorio salvo en existencia inicial" }),
    field({ name: "notas", label: "Observaciones", type: "textarea" }));
  const save = h("button", { class: "btn primary", type: "submit" }, "Registrar");
  form.append(h("div", { class: "row end" }, save));
  let key = newIdempotencyKey();
  const typeSel = form.querySelector("select[name=tipo]");
  typeSel.onchange = () => renderDynamic();

  function renderDynamic() {
    if (!product) return;
    clear(productInfo, h("strong", {}, product.name), ` — existencia actual ${qty(product.stock, product.base_unit_code)}`);
    const inbound = ["inicial", "ajuste_entrada", "devolucion_cliente"].includes(typeSel.value);
    clear(dynamic,
      field({ name: "product_unit_id", label: "Presentación", type: "select", options: presentationOptions(product) }),
      field({ name: "cantidad", label: "Cantidad", type: "decimal", required: true }),
      inbound && can("costos.ver") ? field({ name: "costo_unitario", label: "Costo por presentación", type: "money", required: typeSel.value === "inicial", hint: "Vacío = costo promedio actual" }) : null,
      inbound ? field({ name: "fecha_vencimiento", label: "Vence", type: "date" }) : null,
      inbound ? field({ name: "lote", label: "Lote" }) : null);
  }

  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!product) { showError({ message: "Seleccione un producto" }); return; }
    const d = readForm(form);
    if (!d) return;
    const body = { product_id: product.id, tipo: d.tipo, cantidad: d.cantidad, product_unit_id: d.product_unit_id ? Number(d.product_unit_id) : null,
      motivo: d.motivo, notas: d.notas, costo_unitario: d.costo_unitario ?? undefined, fecha_vencimiento: d.fecha_vencimiento ?? undefined, lote: d.lote ?? undefined };
    const ok = await confirmDialog({ title: "Confirmar movimiento",
      message: `${MOVEMENT_LABELS[d.tipo]}: ${num(d.cantidad)} de "${product.name}". Los movimientos no se pueden editar; solo revertir.` });
    if (!ok) return;
    try {
      // La misma clave se reutiliza si el usuario reintenta tras un error de red: no se duplica.
      const mv = await submitting(save, () => api.post("/inventario/movimientos", body, { idempotencyKey: key }));
      toast(`Movimiento #${mv.id} registrado. Nuevo saldo: ${num(mv.balance_after)}`);
      key = newIdempotencyKey();
      form.reset(); product = null; clear(dynamic); clear(productInfo, "Seleccione un producto.");
    } catch (err) { applyServerErrors(form, err); }
  };
  el.append(h("div", { class: "card" }, form));
}

function lots(el) {
  const days = h("select", { name: "vence_en_dias", "aria-label": "Vencimiento" },
    h("option", { value: "" }, "Todos los lotes abiertos"), h("option", { value: "0" }, "Vencidos o vencen hoy"),
    h("option", { value: "3" }, "Vencen en 3 días"), h("option", { value: "7" }, "Vencen en 7 días"));
  const t = new DataTable({
    search: false, filters: [days],
    load: (p) => api.get("/inventario/lotes", p),
    columns: [
      { title: "Producto", key: "product_name" }, { title: "Lote", render: (r) => r.code || `#${r.id}` },
      { title: "Recibido", render: (r) => dateTime(r.received_at) }, { title: "Vence", render: (r) => date(r.expiry_date) },
      { title: "Días", num: true, render: (r) => (r.days_to_expiry === null ? "—" : r.days_to_expiry < 0 ? h("span", { class: "badge danger" }, "vencido") : num(r.days_to_expiry)) },
      { title: "Inicial", num: true, render: (r) => num(r.initial_qty) }, { title: "Disponible", num: true, render: (r) => num(r.remaining_qty) },
    ],
  });
  el.append(h("p", { class: "muted small" }, "Las salidas consumen primero los lotes que vencen antes (FEFO)."), t.el);
  t.refresh();
}
