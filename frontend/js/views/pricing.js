import { api } from "../api.js";
import { can } from "../app.js";
import { dateTime, money, pct, signedPct } from "../format.js";
import {
  DataTable, applyServerErrors, badge, clear, confirmDialog, field, h, modal, readForm, showError, submitting, tabs, toast,
} from "../ui.js";
import { pageHeader, productPicker } from "./common.js";

export async function render(el) {
  const content = h("div");
  const items = [{ id: "pendientes", label: "Sugerencias pendientes" }, { id: "todas", label: "Historial de sugerencias" },
    { id: "historial", label: "Historial de precios" }];
  if (can("precios.aprobar")) items.push({ id: "manual", label: "Cambiar precio manualmente" });
  const show = (id) => { clear(content); ({ pendientes: () => suggestions(content, "pendiente"), todas: () => suggestions(content, ""), historial: () => history(content), manual: () => manual(content) })[id](); };
  const gen = h("button", { class: "btn primary", onclick: async (e) => {
    try {
      const r = await submitting(e.currentTarget, () => api.post("/precios/sugerencias/generar", {}));
      toast(`${r.generadas} sugerencia(s) generada(s); ${r.omitidas.length} producto(s) omitido(s)`);
      if (r.omitidas.length) modal({ title: "Productos sin sugerencia", body: h("ul", {}, r.omitidas.map((o) => h("li", {}, `${o.producto}: ${o.motivo}`))) });
      show("pendientes");
    } catch (err) { showError(err); }
  } }, "Generar sugerencias");
  el.append(pageHeader("Precios sugeridos", gen),
    h("div", { class: "notice info small" }, "Precio sugerido = costo real promedio ÷ (1 − margen − comisión) × (1 + impuesto), con redondeo comercial. El precio de mercado es solo una referencia. Las sugerencias no cambian precios hasta que un usuario autorizado las aprueba."),
    tabs(items, show), content);
  show("pendientes");
}

function suggestions(el, estado) {
  const t = new DataTable({
    search: false, load: (p) => api.get("/precios/sugerencias", { ...p, estado }),
    columns: [
      { title: "Producto", render: (s) => h("strong", {}, s.product_name) },
      { title: "Costo", num: true, render: (s) => `${money(s.unit_cost)}/${s.base_unit_code}` },
      { title: "Margen", num: true, render: (s) => pct(s.margin) },
      { title: "Actual", num: true, render: (s) => money(s.current_price) },
      { title: "Sugerido", num: true, render: (s) => h("strong", {}, money(s.suggested_price)) },
      { title: "Cambio", num: true, render: (s) => signedPct(s.change_rate) },
      { title: "Mercado (base)", num: true, render: (s) => money(s.market_price_base) },
      { title: "Advertencias", render: (s) => (s.explanation.advertencias?.length ? h("span", { class: "badge warn" }, `${s.explanation.advertencias.length}`) : h("span", { class: "badge ok" }, "0")) },
      { title: "Estado", render: (s) => badge(s.status) },
      { title: "", actions: true, render: (s) => h("button", { class: "btn small", onclick: () => detail(s, t) }, s.status === "pendiente" && can("precios.aprobar") ? "Revisar" : "Ver") },
    ],
    emptyText: estado === "pendiente" ? "No hay sugerencias pendientes" : "Sin sugerencias",
  });
  el.append(t.el);
  t.refresh();
}

function detail(s, t) {
  const e = s.explanation;
  const body = h("div", { class: "stack" },
    h("dl", { class: "explain" },
      h("dt", {}, "Fórmula"), h("dd", {}, e.formula),
      h("dt", {}, "Costo unitario"), h("dd", {}, `${money(e.costo_unitario)} por ${e.unidad} (${e.base_costo})`),
      h("dt", {}, "Margen / comisión / impuesto"), h("dd", {}, `${pct(e.margen)} / ${pct(e.comision)} / ${pct(e.impuesto)}`),
      h("dt", {}, "Precio sin redondeo → sugerido"), h("dd", {}, `${money(e.precio_sin_redondeo)} → ${money(s.suggested_price)} (${e.redondeo})`),
      h("dt", {}, "Precio vigente"), h("dd", {}, `${money(s.current_price)} (${signedPct(s.change_rate)})`),
      h("dt", {}, "Referencia de mercado"), h("dd", {}, e.mercado ? `${e.mercado.producto_fuente} en ${e.mercado.fuente_mercado}, ${e.mercado.fecha}: ${money(e.mercado.precio_por_unidad_base)} por ${e.unidad}; costo vs. mercado ${signedPct(e.mercado.diferencia_costo_vs_mercado)}` : "Sin referencia utilizable")),
    e.advertencias?.length ? h("div", { class: "notice warn" }, h("strong", {}, "Advertencias"), h("ul", {}, e.advertencias.map((w) => h("li", {}, w)))) : null,
    h("p", { class: "muted small" }, `Generada ${dateTime(s.created_at)} (${s.origin})`),
    s.decision_note ? h("p", {}, `Decisión: ${s.decision_note} (${dateTime(s.decided_at)})`) : null);
  const actions = [];
  let m;
  if (s.status === "pendiente" && can("precios.aprobar")) {
    const form = h("form", { class: "form card", novalidate: true },
      field({ name: "note", label: "Justificación", type: "textarea", required: true }),
      field({ name: "final_price", label: "Precio final (opcional, si difiere del sugerido)", type: "money" }));
    body.append(form);
    const approve = h("button", { class: "btn primary", type: "button" }, "Aprobar");
    const reject = h("button", { class: "btn danger", type: "button" }, "Rechazar");
    const decide = async (btn, action) => {
      const d = readForm(form);
      if (!d) return;
      if (action === "aprobar") {
        const ok = await confirmDialog({ title: "Aprobar cambio de precio", message: `${s.product_name}: ${money(s.current_price)} → ${money(d.final_price || s.suggested_price)} por ${s.base_unit_code}.`, confirmText: "Aprobar" });
        if (!ok) return;
      }
      try {
        await submitting(btn, () => api.post(`/precios/sugerencias/${s.id}/${action}`, { note: d.note, final_price: action === "aprobar" ? d.final_price : undefined }));
        toast(action === "aprobar" ? "Precio actualizado" : "Sugerencia rechazada"); m.close(); t.refresh();
      } catch (err) { applyServerErrors(form, err); }
    };
    approve.onclick = () => decide(approve, "aprobar");
    reject.onclick = () => decide(reject, "rechazar");
    actions.push(reject, approve);
  }
  m = modal({ title: `Sugerencia: ${s.product_name}`, body, actions, wide: true });
}

function history(el) {
  let productId = null;
  const t = new DataTable({
    search: false, load: (p) => api.get("/precios/historial", { ...p, product_id: productId }),
    columns: [
      { title: "Fecha", render: (r) => dateTime(r.changed_at) }, { title: "Producto", key: "product_name" },
      { title: "Anterior", num: true, render: (r) => money(r.old_price) }, { title: "Nuevo", num: true, render: (r) => money(r.new_price) },
      { title: "Origen", key: "source" }, { title: "Justificación", key: "justification" }, { title: "Usuario", key: "changed_by_name" },
    ],
  });
  el.append(h("div", { class: "toolbar" }, h("div", { class: "grow" }, productPicker({ activeOnly: false, onPick: (p) => { productId = p.id; t.refresh(1); } })),
    h("button", { class: "btn small ghost", onclick: () => { productId = null; t.refresh(1); } }, "Todos")), t.el);
  t.refresh();
}

function manual(el) {
  let product = null;
  const info = h("div", { class: "notice info" }, "Seleccione un producto.");
  const form = h("form", { class: "form", novalidate: true },
    productPicker({ onPick: (p) => { product = p; clear(info, h("strong", {}, p.name), ` — precio actual ${money(p.current_price)} por ${p.base_unit_code}`); } }), info,
    h("div", { class: "cols" }, field({ name: "new_price", label: "Nuevo precio por unidad base (impuestos incluidos)", type: "money", required: true })),
    field({ name: "justification", label: "Justificación", type: "textarea", required: true }));
  const save = h("button", { class: "btn primary", type: "submit" }, "Cambiar precio");
  form.append(h("div", { class: "row end" }, save));
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!product) { showError({ message: "Seleccione un producto" }); return; }
    const d = readForm(form);
    if (!d) return;
    const send = (confirm_large_change) => api.post(`/precios/productos/${product.id}`, { ...d, confirm_large_change });
    try {
      await submitting(save, () => send(false));
      toast("Precio actualizado"); form.reset(); product = null; clear(info, "Seleccione un producto.");
    } catch (err) {
      if (err.code === "variacion_requiere_confirmacion") {
        const ok = await confirmDialog({ title: "Variación grande", message: err.message, confirmText: "Confirmar cambio", danger: true });
        if (ok) { try { await send(true); toast("Precio actualizado"); form.reset(); } catch (e2) { showError(e2); } }
      } else applyServerErrors(form, err);
    }
  };
  el.append(h("div", { class: "card" }, form));
}
