import { api, newIdempotencyKey } from "../api.js";
import { can, session } from "../app.js";
import { dateTime, decCmp, decDivExact, decMul, decSum, money, num, parseDecimal, pct, qty, todayISO } from "../format.js";
import {
  DataTable, badge, clear, confirmDialog, errorText, h, loadingEl, modal, showError, simpleTable, submitting, tabs, toast,
} from "../ui.js";
import { pageHeader, productPicker, units } from "./common.js";

const PAYMENT = [["efectivo", "Efectivo"], ["transferencia", "Transferencia"], ["tarjeta", "Tarjeta"], ["otro", "Otro"]];
const WEIGHT_UNITS = ["g", "kg"];  // unidades de captura en la báscula (deben existir en el catálogo de unidades)

// Preferencias de esta pantalla (solo comodidad del cajero; el navegador puede no permitir guardarlas).
const pref = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento local */ } },
};

export async function render(el) {
  const content = h("div");
  const items = [];
  if (can("ventas.registrar")) items.push({ id: "pos", label: "Registrar venta" });
  items.push({ id: "historial", label: "Historial de ventas" });
  const show = (id) => { clear(content); (id === "pos" ? pos : history)(content); };
  el.append(pageHeader("Ventas"), tabs(items, show), content);
  show(items[0].id);
}

// ------------------------------------------------------------------ cálculo local (solo para mostrar)
// El servidor recalcula todo al previsualizar y al registrar; aquí se replica la misma regla con
// decimales exactos para que el importe aparezca al instante mientras se escribe el peso.

/** Formas de vender un producto: por peso (g/kg) si su unidad base es de masa, y sus presentaciones. */
function saleOptions(p, unitList) {
  const base = unitList.find((u) => u.id === p.base_unit_id);
  const basePu = p.presentations.find((x) => x.is_base && x.is_active && x.for_sale);
  const byWeight = base?.kind === "masa" && !!basePu;
  const opts = [];
  if (byWeight) {
    for (const code of WEIGHT_UNITS) {
      const u = unitList.find((x) => x.code === code && x.kind === "masa" && x.is_active);
      if (u) opts.push({ key: `w:${code}`, label: code, weightUnit: code, unit: u });
    }
  } else if (basePu) opts.push({ key: `p:${basePu.id}`, label: basePu.name, pu: basePu });
  for (const x of p.presentations) {
    if (!x.is_base && x.is_active && x.for_sale) opts.push({ key: `p:${x.id}`, label: x.name, pu: x, hint: `= ${qty(x.factor_to_base, p.base_unit_code)}` });
  }
  return { byWeight, base, opts };
}

/** Cantidad en unidad base (4 decimales) o null si no es representable (el servidor también la rechaza). */
function baseQty(info, opt, q) {
  if (opt.weightUnit) return decDivExact(decMul([q, opt.unit.kg_equivalent], 12), info.base.kg_equivalent, 4);
  return decMul([q, opt.pu.factor_to_base], 4);
}

/** Importe = cantidad × factor × precio por unidad base, redondeado una vez a centavos (igual que el servidor). */
const lineAmount = (p, opt, q, base) => (opt.weightUnit ? decMul([base, p.current_price], 2) : decMul([q, opt.pu.factor_to_base, p.current_price], 2));

function describeQty(line) {
  const code = line.product.base_unit_code;
  if (line.opt.weightUnit) return line.opt.weightUnit === code ? qty(line.qtyText, code) : `${qty(line.qtyText, line.opt.weightUnit)} (${qty(line.base, code)})`;
  return line.opt.pu.is_base ? qty(line.qtyText, code) : `${qty(line.qtyText)} × ${line.opt.pu.name}`;
}

// ------------------------------------------------------------------ punto de venta

function pos(el) {
  const cart = [];  // { id, product, info, opt, qtyText, base, estimate, discount, expectedPrice, subEl, rowEl }
  let unitList = [];
  let current = null;  // producto en la báscula: { product, info, opt, editing }
  let payment = pref.get("pos.payment", "efectivo");
  let key = newIdempotencyKey();
  let lastPreview = null;
  let previewedFor = null;  // carrito (serializado) al que corresponde la última previsualización
  let timer;
  let busy = false;
  const unitsReady = units().then((u) => { unitList = u; }).catch(showError);

  // --- búsqueda y accesos rápidos
  const picker = productPicker({ onPick: (p) => select(p), showPrice: true });
  const focusSearch = () => picker.querySelector("input").focus();
  const quick = h("div", { class: "quick" });
  api.get("/ventas/frecuentes").then((list) => {
    if (!list.length) return;
    clear(quick, h("div", { class: "muted small quick-title" }, "Frecuentes"), list.map((p) => h("button", { type: "button", class: "btn chip", onclick: () => select(p) },
      h("strong", {}, p.name), h("span", { class: "muted small" }, `${money(p.current_price)}/${p.base_unit_code}`))));
  }).catch(() => { /* los accesos rápidos son opcionales */ });

  // --- báscula
  const scale = h("section", { class: "card scale", "aria-label": "Captura de peso o cantidad" });
  const qtyInput = h("input", { class: "scale-input", inputmode: "decimal", autocomplete: "off", "aria-label": "Peso o cantidad", placeholder: "0" });
  const unitBar = h("div", { class: "seg", role: "radiogroup", "aria-label": "Unidad de captura" });
  const normEl = h("div", { class: "muted scale-norm", "aria-live": "polite" });
  const amountEl = h("div", { class: "scale-amount", "aria-live": "polite" }, money(0));
  const stockEl = h("div", { class: "small" });
  const addBtn = h("button", { class: "btn primary big", type: "button", disabled: true, onclick: () => addLine() }, "Agregar a la venta");
  const cancelBtn = h("button", { class: "btn big", type: "button", onclick: () => { current = null; renderScale(); focusSearch(); } }, "Cancelar");
  const press = (k) => {
    if (k === "⌫") qtyInput.value = qtyInput.value.slice(0, -1);
    else if (k === "C") qtyInput.value = "";
    else if (k !== "," || !/[.,]/.test(qtyInput.value)) qtyInput.value += k;
    update();
  };
  const keypad = h("div", { class: "keypad", role: "group", "aria-label": "Teclado numérico" },
    ["7", "8", "9", "4", "5", "6", "1", "2", "3", ",", "0", "⌫"].map((k) => h("button", { type: "button", class: "btn", "aria-label": k === "⌫" ? "Borrar" : k, onclick: () => press(k) }, k)),
    h("button", { type: "button", class: "btn wide", onclick: () => press("C") }, "Limpiar"));
  qtyInput.addEventListener("input", () => update());
  qtyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addLine(); } });

  async function select(p, line = null) {
    if (!p.current_price || decCmp(p.current_price, "0") <= 0) { showError({ message: `"${p.name}" no tiene precio de venta aprobado` }); return; }
    await unitsReady;
    const info = saleOptions(p, unitList);
    if (!info.opts.length) { showError({ message: `"${p.name}" no tiene presentaciones habilitadas para la venta` }); return; }
    const wanted = line?.opt.key ?? (info.byWeight ? `w:${pref.get("pos.weightUnit", "g")}` : null);
    current = { product: p, info, opt: info.opts.find((o) => o.key === wanted) ?? info.opts[0], editing: line };
    qtyInput.value = line ? line.qtyText.replace(".", ",") : "";
    clear(doneEl);
    renderScale();
    qtyInput.focus();
  }

  /** Lee la captura: { q, base, amount } si es válida, { error } si no, o null si está vacía. */
  function entry() {
    const raw = qtyInput.value.trim();
    if (!raw) return null;
    // Como en una báscula: un único separador decimal ("," o ".") y sin separador de miles.
    // Así "1.250" en kg es 1,25 kg y no 1.250 kg.
    if (!/^\d*([.,]\d*)?$/.test(raw) || !/\d/.test(raw)) return { error: "Escriba solo números, sin separador de miles (use coma para los decimales)." };
    const q = raw.replace(",", ".").replace(/^\./, "0.").replace(/\.$/, "").replace(/^0+(?=\d)/, "");
    if (decCmp(q, "0") <= 0) return { error: current.info.byWeight ? "El peso debe ser mayor que cero." : "La cantidad debe ser mayor que cero." };
    if ((q.split(".")[1] || "").length > 4) return { error: "Use como máximo 4 decimales." };
    const base = baseQty(current.info, current.opt, q);
    if (base === null || decCmp(base, "0") <= 0) return { error: `La cantidad es demasiado pequeña (mínimo 0,0001 ${current.product.base_unit_code}).` };
    return { q, base, amount: lineAmount(current.product, current.opt, q, base) };
  }

  function update() {
    if (!current) return;
    const e = entry();
    const p = current.product;
    const code = p.base_unit_code;
    addBtn.disabled = !e || !!e.error;
    amountEl.textContent = money(e && !e.error ? e.amount : 0);
    if (!e) clear(normEl, current.info.byWeight ? `Escriba el peso en ${current.opt.label}` : "Escriba la cantidad");
    else if (e.error) clear(normEl, h("span", { class: "text-danger" }, e.error));
    else clear(normEl, current.opt.weightUnit === code || current.opt.pu?.is_base ? `${qty(e.base, code)} × ${money(p.current_price)}/${code}` : `= ${qty(e.base, code)} × ${money(p.current_price)}/${code}`);
    // Aviso de existencias (orientativo: la validación definitiva la hace el servidor al registrar).
    const inCart = decSum(cart.filter((c) => c.product.id === p.id && c !== current.editing).map((c) => c.base), 4);
    const wanted = decSum([inCart, e && !e.error ? e.base : "0"], 4);
    const short = decCmp(wanted, p.stock) > 0;
    stockEl.className = `small ${short ? "text-danger" : "muted"}`;
    clear(stockEl, `Existencia: ${qty(p.stock, code)}`, decCmp(inCart, "0") > 0 ? ` · en esta venta: ${qty(inCart, code)}` : "",
      short ? h("strong", {}, " · Supera la existencia disponible") : "");
  }

  function renderScale() {
    if (!current) {
      clear(scale, h("div", { class: "empty" }, "Busque un producto o elija uno frecuente para pesarlo o contarlo.",
        h("div", { class: "small" }, "Atajos: F2 buscar · Enter agregar · Esc cancelar")));
      return;
    }
    const p = current.product;
    clear(unitBar, current.info.opts.map((o) => h("button", { type: "button", role: "radio", "aria-checked": String(o === current.opt),
      class: `btn seg-btn${o === current.opt ? " primary" : ""}`, title: o.hint || null,
      onclick: () => { current.opt = o; if (o.weightUnit) pref.set("pos.weightUnit", o.weightUnit); renderScale(); qtyInput.focus(); } }, o.label)));
    clear(scale,
      h("div", { class: "scale-head" },
        h("div", {}, h("h2", {}, p.name, p.variety ? h("span", { class: "muted" }, ` · ${p.variety}`) : ""),
          h("span", { class: `badge ${current.info.byWeight ? "info" : ""}` }, current.info.byWeight ? "Se vende por peso" : "Se vende por unidad o presentación")),
        h("div", { class: "scale-price" }, `${money(p.current_price)} / ${p.base_unit_code}`)),
      h("div", { class: "scale-grid" },
        h("div", { class: "stack" },
          h("label", { class: "field" }, h("span", { class: "label" }, current.info.byWeight ? "Peso" : "Cantidad"), qtyInput),
          unitBar, normEl,
          h("div", {}, h("div", { class: "muted small" }, "Importe"), amountEl),
          stockEl,
          h("div", { class: "row" }, addBtn, cancelBtn)),
        keypad));
    addBtn.textContent = current.editing ? "Actualizar línea" : "Agregar a la venta";
    update();
  }

  function addLine() {
    const e = current && entry();
    if (!e || e.error) { qtyInput.focus(); return; }
    const line = current.editing ?? { id: crypto.randomUUID(), discount: "0" };
    Object.assign(line, { product: current.product, info: current.info, opt: current.opt, qtyText: e.q, base: e.base,
      estimate: e.amount, expectedPrice: current.product.current_price });
    if (!current.editing) cart.push(line);
    current = null;
    renderScale();
    renderCart();
    focusSearch();
  }

  // --- venta actual
  const cartList = h("ul", { class: "cart-list", "aria-label": "Productos de la venta" });
  const totalEl = h("div", { class: "total", "aria-live": "polite" }, money(0));
  const summary = h("div");
  const doneEl = h("div", { "aria-live": "polite" });
  const payBar = h("div", { class: "seg", role: "radiogroup", "aria-label": "Medio de pago" });
  const customer = h("input", { placeholder: "Cliente (opcional)", maxlength: 120, "aria-label": "Cliente" });
  const reference = h("input", { placeholder: "Referencia (opcional)", maxlength: 60, "aria-label": "Referencia" });
  const confirmBtn = h("button", { class: "btn primary big block", type: "button", disabled: true }, "Cobrar");
  customer.addEventListener("input", schedule);
  reference.addEventListener("input", schedule);

  const renderPayment = () => clear(payBar, PAYMENT.map(([v, l]) => h("button", { type: "button", role: "radio", "aria-checked": String(v === payment),
    class: `btn seg-btn${v === payment ? " primary" : ""}`, onclick: () => { payment = v; pref.set("pos.payment", v); renderPayment(); schedule(); } }, l)));

  function renderCart() {
    clear(cartList, cart.length ? cart.map((c) => {
      const disc = h("input", { inputmode: "decimal", value: c.discount === "0" ? "" : c.discount.replace(".", ","), placeholder: "0", "aria-label": `Descuento en pesos para ${c.product.name}` });
      disc.addEventListener("input", () => { c.discount = parseDecimal(disc.value) ?? "0"; schedule(); });
      c.subEl = h("div", { class: "sub" }, money(c.estimate));
      c.rowEl = h("li", { class: "cart-line" },
        h("div", {}, h("strong", {}, c.product.name), h("div", { class: "muted small" }, `${describeQty(c)} · ${money(c.expectedPrice)}/${c.product.base_unit_code}`)),
        c.subEl,
        h("div", { class: "line-actions" },
          h("label", { class: "small muted" }, "Desc. $ ", disc),
          h("span", { class: "spacer" }),
          h("button", { type: "button", class: "btn small", onclick: () => select(c.product, c) }, "Editar"),
          h("button", { type: "button", class: "btn small ghost", "aria-label": `Quitar ${c.product.name}`, onclick: () => { cart.splice(cart.indexOf(c), 1); if (current?.editing === c) { current = null; renderScale(); } renderCart(); } }, "✕")));
      return c.rowEl;
    }) : h("li", { class: "empty" }, "Aún no hay productos en la venta."));
    schedule();
  }

  const payload = () => ({
    payment_method: payment, customer_name: customer.value.trim() || null, reference: reference.value.trim() || null,
    lines: cart.map((c) => ({ product_id: c.product.id, product_unit_id: c.opt.pu?.id ?? null, quantity: c.qtyText,
      weight_unit: c.opt.weightUnit, discount: c.discount, expected_price: c.expectedPrice })),
  });

  function schedule() {
    // Un evento que no cambia el carrito no debe deshabilitar el botón.
    if (cart.length && JSON.stringify(payload()) === previewedFor) return;
    confirmBtn.disabled = true;
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  }

  async function refresh() {
    lastPreview = null;
    previewedFor = null;
    confirmBtn.textContent = "Cobrar";
    if (!cart.length) { totalEl.textContent = money(0); clear(summary); return; }
    totalEl.textContent = money(decSum(cart.map((c) => c.estimate)));
    const d = payload();
    clear(summary, h("div", { class: "muted small" }, h("span", { class: "spinner" }), " Verificando precios y existencias…"));
    let r;
    try { r = await api.post("/ventas/previsualizar", d); } catch (e) { clear(summary, h("div", { class: "notice warn small" }, errorText(e), " ", retryBtn())); return; }
    if (JSON.stringify(payload()) !== JSON.stringify(d)) return;  // el carrito cambió mientras se consultaba
    lastPreview = r;
    previewedFor = JSON.stringify(d);
    r.lines.forEach((l, i) => {
      const c = cart[i];
      c.subEl.textContent = money(l.net_amount);
      c.rowEl.classList.toggle("problem", !l.stock_ok || l.price_changed);
    });
    totalEl.textContent = money(r.total);
    const short = r.lines.filter((l) => !l.stock_ok);
    const changed = r.lines.map((l, i) => ({ l, c: cart[i] })).filter((x) => x.l.price_changed);
    clear(summary,
      h("dl", { class: "sum-rows" },
        h("dt", {}, "Subtotal"), h("dd", {}, money(r.gross_total)),
        decCmp(r.discount_total, "0") > 0 ? [h("dt", {}, "Descuentos"), h("dd", {}, `− ${money(r.discount_total)} (${pct(r.discount_rate)})`)] : null,
        h("dt", {}, "Impuestos incluidos"), h("dd", {}, money(r.tax_total))),
      changed.length ? h("div", { class: "notice warn small" }, "El precio cambió después de agregar: ",
        changed.map((x) => `${x.c.product.name} ${money(x.c.expectedPrice)} → ${money(x.l.price_base)}`).join("; "), ". ",
        h("button", { type: "button", class: "btn small", onclick: () => {
          for (const x of changed) { x.c.product = { ...x.c.product, current_price: x.l.price_base }; x.c.expectedPrice = x.l.price_base; x.c.estimate = lineAmount(x.c.product, x.c.opt, x.c.qtyText, x.c.base); }
          renderCart();
        } }, "Usar precios vigentes")) : null,
      !r.discount_authorized ? h("div", { class: "notice danger small" }, `El descuento supera el máximo autorizado (${pct(r.max_discount_rate)}). Solicite autorización a un administrador.`) : null,
      short.length ? h("div", { class: "notice danger small" }, "Existencias insuficientes: ",
        short.map((l) => `${l.product_name} (disponible ${num(l.available)})`).join("; "), ". Corrija la cantidad o quite el producto.") : null);
    const ok = r.discount_authorized && !short.length && !changed.length;
    confirmBtn.disabled = !ok;
    confirmBtn.textContent = `Cobrar ${money(r.total)}`;
  }

  const retryBtn = () => h("button", { type: "button", class: "btn small", onclick: () => { previewedFor = null; refresh(); } }, "Reintentar");

  function resetSale() {
    cart.splice(0);
    customer.value = "";
    reference.value = "";
    renderCart();
  }

  confirmBtn.onclick = async () => {
    const d = payload();
    if (!lastPreview || busy || JSON.stringify(d) !== previewedFor) return;
    const preview = lastPreview;
    const payLabel = PAYMENT.find(([v]) => v === payment)[1];
    const ok = await confirmDialog({ title: "Confirmar venta",
      message: `${cart.length} ${cart.length === 1 ? "línea" : "líneas"} · Total a cobrar ${money(preview.total)} · Pago: ${payLabel}.`, confirmText: "Registrar venta" });
    if (!ok) return;
    busy = true;
    try {
      const s = await submitting(confirmBtn, () => api.post("/ventas", d, { idempotencyKey: key }));
      key = newIdempotencyKey();
      if (decCmp(s.total, preview.total) !== 0 || s.lines.length !== d.lines.length) {
        // Respuesta de un intento anterior con la misma clave (p. ej. se perdió la conexión y luego se editó la venta).
        clear(doneEl, h("div", { class: "notice warn" }, `La venta #${s.id} ya estaba registrada por ${money(s.total)} desde un intento anterior. `,
          "Los cambios posteriores del carrito NO se registraron: revíselos y cobre la diferencia como una venta nueva.", " ", printBtn(s)));
        previewedFor = null;
        refresh();
      } else {
        clear(doneEl, h("div", { class: "notice ok" }, h("strong", {}, `Venta #${s.id} registrada`), ` · ${money(s.total)} · ${payLabel} `, printBtn(s)));
        toast(`Venta #${s.id} registrada por ${money(s.total)}`);
        resetSale();
        focusSearch();
      }
    } catch (e) {
      if (e.status === 0) {
        clear(doneEl, h("div", { class: "notice danger" }, "No hubo respuesta del servidor; la venta pudo o no registrarse. ",
          "Pulse «Cobrar» de nuevo cuando vuelva la conexión: si ya se registró, no se duplicará."));
      } else {
        showError(e);
        if (["precio_cambiado", "existencias_insuficientes", "producto_invalido", "sin_precio"].includes(e.code)) { previewedFor = null; refresh(); }
      }
    } finally { busy = false; }
  };

  const root = h("div", { class: "pos" },
    h("div", { class: "pos-col" }, h("div", { class: "card stack" }, picker, quick), scale),
    h("div", { class: "pos-col" },
      h("section", { class: "card stack", "aria-label": "Venta actual" }, h("div", { class: "row between" }, h("h2", {}, "Venta actual"), totalEl),
        cartList, summary, payBar, h("details", {}, h("summary", { class: "small muted" }, "Cliente y referencia"), h("div", { class: "stack" }, customer, reference)),
        confirmBtn, doneEl,
        h("p", { class: "muted small" }, "Precios y existencias los valida el servidor al cobrar. Registro interno: no es factura electrónica."))));

  const onKey = (e) => {
    if (!root.isConnected) { document.removeEventListener("keydown", onKey); return; }
    if (document.querySelector("dialog[open]")) return;
    if (e.key === "F2") { e.preventDefault(); focusSearch(); }
    if (e.key === "Escape" && current) { current = null; renderScale(); focusSearch(); }
  };
  document.addEventListener("keydown", onKey);

  renderPayment();
  renderScale();
  renderCart();
  el.append(root);
  setTimeout(focusSearch, 0);  // después de que el enrutador enfoque <main>: el cajero empieza buscando
}

// ------------------------------------------------------------------ comprobante

const printBtn = (s) => h("button", { type: "button", class: "btn small", onclick: () => printReceipt(s) }, "Imprimir comprobante");

function printReceipt(s) {
  const rows = s.lines.flatMap((l) => [
    h("tr", {}, h("td", { colspan: 2 }, l.product_name)),
    h("tr", {}, h("td", { class: "small" }, `${qty(l.quantity)} ${l.unit_name} × ${money(l.unit_price)}`, decCmp(l.discount, "0") > 0 ? ` (−${money(l.discount)})` : ""),
      h("td", { class: "num" }, money(l.net_amount)))]);
  const r = h("div", { class: "receipt" },
    h("h2", {}, "Comprobante interno de venta"),
    h("p", {}, `Venta #${s.id}`, h("br"), dateTime(s.sold_at), h("br"), `Atendió: ${s.created_by_name}`, s.customer_name ? [h("br"), `Cliente: ${s.customer_name}`] : null),
    h("table", {}, h("tbody", {}, rows)),
    h("table", { class: "totals" }, h("tbody", {},
      decCmp(s.discount_total, "0") > 0 ? h("tr", {}, h("td", {}, "Descuentos"), h("td", { class: "num" }, `−${money(s.discount_total)}`)) : null,
      h("tr", {}, h("td", {}, h("strong", {}, "TOTAL")), h("td", { class: "num" }, h("strong", {}, money(s.total)))),
      h("tr", {}, h("td", {}, "Impuestos incluidos"), h("td", { class: "num" }, money(s.tax_total))),
      h("tr", {}, h("td", {}, "Pago"), h("td", { class: "num" }, (PAYMENT.find(([v]) => v === s.payment_method) || [, s.payment_method])[1])))),
    s.status === "anulada" ? h("p", {}, h("strong", {}, "VENTA ANULADA")) : null,
    h("p", { class: "small" }, "Registro interno. No es factura electrónica."));
  const done = () => { r.remove(); document.body.classList.remove("printing"); window.removeEventListener("afterprint", done); };
  document.body.append(r);
  document.body.classList.add("printing");
  window.addEventListener("afterprint", done);
  window.print();
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
    h("div", { class: "row" }, printBtn(s)),
    s.status === "confirmada" && can("ventas.anular") ? h("div", { class: "row end" }, h("button", { class: "btn danger", onclick: async () => {
      const reason = await confirmDialog({ title: "Anular venta", danger: true, confirmText: "Anular venta",
        message: "La mercancía volverá al inventario con un movimiento compensatorio. La venta queda registrada como anulada.", input: { label: "Motivo", minLength: 3 } });
      if (!reason) return;
      try { await api.post(`/ventas/${id}/anular`, { reason }); toast("Venta anulada"); m.close(); t.refresh(); } catch (e) { showError(e); }
    } }, "Anular venta")) : null));
}
