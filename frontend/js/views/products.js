import { api } from "../api.js";
import { can } from "../app.js";
import { dateTime, money, pct, qty } from "../format.js";
import {
  DataTable, applyServerErrors, clear, confirmDialog, field, h, loadingEl, modal, readForm, showError,
  simpleTable, submitting, toast,
} from "../ui.js";
import { categories, opt, pageHeader, suppliers, units } from "./common.js";

let table;

export async function render(el) {
  const [cats, sups] = await Promise.all([categories(), suppliers()]);
  const filters = [
    h("select", { name: "categoria_id", "aria-label": "Categoría" }, opt(cats, "name", "Todas las categorías").map((o) => h("option", { value: o.value }, o.label))),
    h("select", { name: "proveedor_id", "aria-label": "Proveedor" }, opt(sups, "name", "Todos los proveedores").map((o) => h("option", { value: o.value }, o.label))),
    h("select", { name: "activo", "aria-label": "Estado" }, h("option", { value: "true" }, "Activos"), h("option", { value: "false" }, "Inactivos")),
    h("label", { class: "check small" }, h("input", { type: "checkbox", name: "bajo_minimo" }), "Solo bajo mínimo"),
  ];
  const costs = can("costos.ver");
  table = new DataTable({
    filters,
    searchPlaceholder: "Buscar por nombre, SKU, variedad o código de barras…",
    load: (p) => api.get("/productos", p),
    rowClass: (r) => (r.is_active ? "" : "inactive"),
    columns: [
      { title: "SKU", key: "sku", sort: "sku" },
      { title: "Producto", sort: "name", render: (r) => h("span", {}, h("strong", {}, r.name), r.variety ? ` · ${r.variety}` : "", h("div", { class: "muted small" }, r.category_name)) },
      { title: "Existencia", num: true, sort: "-stock", sortDesc: "stock", render: (r) => h("span", {}, qty(r.stock, r.base_unit_code), r.low_stock ? h("span", { class: "badge warn" }, " bajo") : null) },
      { title: "Precio venta", num: true, render: (r) => (r.current_price ? `${money(r.current_price)}/${r.base_unit_code}` : h("span", { class: "badge warn" }, "sin precio")) },
      ...(costs ? [{ title: "Costo prom.", num: true, render: (r) => money(r.avg_cost) },
        { title: "Margen actual", num: true, render: (r) => pct(r.gross_margin_at_current_price) }] : []),
      { title: "", actions: true, render: (r) => actions(r) },
    ],
  });
  el.append(pageHeader("Productos", can("productos.gestionar") ? h("button", { class: "btn primary", onclick: () => editProduct() }, "+ Nuevo producto") : null), table.el);
  await table.refresh();
}

function actions(p) {
  return h("span", {},
    h("button", { class: "btn small", onclick: () => detail(p.id) }, "Ver"),
    can("productos.gestionar") ? h("button", { class: "btn small", onclick: () => editProduct(p) }, "Editar") : null,
    can("productos.gestionar") ? h("button", { class: "btn small ghost", onclick: () => toggleActive(p) }, p.is_active ? "Desactivar" : "Activar") : null);
}

async function toggleActive(p) {
  const ok = await confirmDialog({
    title: p.is_active ? "Desactivar producto" : "Activar producto",
    message: p.is_active ? `"${p.name}" no aparecerá en ventas ni compras. Su historial se conserva (no se elimina).` : `"${p.name}" volverá a estar disponible.`,
    confirmText: p.is_active ? "Desactivar" : "Activar", danger: p.is_active,
  });
  if (!ok) return;
  try {
    await api.post(`/productos/${p.id}/${p.is_active ? "desactivar" : "activar"}`);
    toast(p.is_active ? "Producto desactivado" : "Producto activado");
    table.refresh();
  } catch (e) { showError(e); }
}

async function editProduct(p = null) {
  const [cats, us, sups] = await Promise.all([categories(), units(), suppliers()]);
  const unitOpts = us.filter((u) => u.is_active).map((u) => ({ value: u.id, label: `${u.name} (${u.code})` }));
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "cols" },
      field({ name: "sku", label: "Código interno (SKU)", required: true, value: p?.sku, pattern: "[A-Za-z0-9._-]{1,40}", hint: "Letras, números, punto, guion" }),
      field({ name: "barcode", label: "Código de barras", value: p?.barcode }),
      field({ name: "name", label: "Nombre comercial", required: true, value: p?.name }),
      field({ name: "variety", label: "Variedad", value: p?.variety })),
    h("div", { class: "cols" },
      field({ name: "category_id", label: "Categoría", type: "select", required: true, value: p?.category_id, options: opt(cats.filter((c) => c.is_active), "name", "Seleccione…") }),
      p ? h("div", { class: "field" }, h("label", {}, "Unidad base"), h("input", { value: p.base_unit_code, disabled: true }), h("div", { class: "hint" }, "No se puede cambiar: el inventario se lleva en esta unidad"))
        : field({ name: "base_unit_id", label: "Unidad base de inventario", type: "select", required: true, options: [{ value: "", label: "Seleccione…" }, ...unitOpts], hint: "Normalmente kg o unidad" }),
      field({ name: "min_stock", label: "Stock mínimo (unidad base)", type: "decimal", value: p?.min_stock ?? "0" }),
      field({ name: "usual_supplier_id", label: "Proveedor habitual", type: "select", value: p?.usual_supplier_id, options: opt(sups, "name", "—") })),
    h("div", { class: "cols" },
      field({ name: "tax_rate", label: "Tasa de impuesto (0 a 0,99)", type: "decimal", value: p?.tax_rate ?? "0", hint: "Ej.: 0,19 para 19 %. Frutas y verduras frescas: confirme con su contador." }),
      can("configuracion.gestionar") ? field({ name: "target_margin", label: "Margen objetivo (0 a 0,99)", type: "decimal", value: p?.target_margin, hint: "Vacío = margen de la categoría o el general" }) : null,
      !p && can("precios.aprobar") ? field({ name: "current_price", label: "Precio de venta inicial por unidad base", type: "money", hint: "Impuestos incluidos" }) : null,
      field({ name: "is_perishable", label: "Producto perecedero", type: "checkbox", value: p ? p.is_perishable : true })),
    field({ name: "description", label: "Descripción", type: "textarea", value: p?.description }),
    !p ? h("p", { class: "muted small" }, "Después de crear el producto podrá agregar presentaciones (bulto, caja, canastilla…) con su contenido exacto.") : null);
  const save = h("button", { class: "btn primary", type: "submit" }, p ? "Guardar cambios" : "Crear producto");
  form.append(h("div", { class: "row end" }, save));
  const m = modal({ title: p ? `Editar: ${p.name}` : "Nuevo producto", body: form, wide: true });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    for (const k of ["category_id", "base_unit_id", "usual_supplier_id"]) if (d[k]) d[k] = Number(d[k]);
    if (!d.min_stock) d.min_stock = "0";
    if (!d.tax_rate) d.tax_rate = "0";
    try {
      const saved = await submitting(save, () => (p ? api.patch(`/productos/${p.id}`, d) : api.post("/productos", d)));
      toast(p ? "Producto actualizado" : "Producto creado");
      m.close();
      table.refresh();
      if (!p) detail(saved.id);
    } catch (err) { applyServerErrors(form, err); }
  };
}

async function detail(id) {
  const body = h("div", {}, loadingEl());
  const m = modal({ title: "Producto", body, wide: true });
  try {
    const p = await api.get(`/productos/${id}`);
    m.dlg.querySelector("#dlg-title").textContent = `${p.name}${p.variety ? " · " + p.variety : ""}`;
    const sections = [
      h("div", { class: "grid kpis" },
        h("div", { class: "card kpi" }, h("div", { class: "label" }, "Existencia"), h("div", { class: "value" }, qty(p.stock, p.base_unit_code))),
        h("div", { class: "card kpi" }, h("div", { class: "label" }, "Precio de venta"), h("div", { class: "value" }, p.current_price ? money(p.current_price) : "—"), h("div", { class: "hint" }, `por ${p.base_unit_code}, impuestos incluidos`)),
        "avg_cost" in p ? h("div", { class: "card kpi" }, h("div", { class: "label" }, "Costo promedio"), h("div", { class: "value" }, money(p.avg_cost)), h("div", { class: "hint" }, `Margen objetivo efectivo: ${pct(p.effective_margin)}`)) : null),
      h("h3", {}, "Presentaciones y conversiones"),
      presentationsTable(p),
    ];
    if (can("productos.gestionar")) sections.push(addPresentationForm(p, () => { m.close(); detail(id); }));
    if (can("precios.ver")) {
      const hist = await api.get("/precios/historial", { product_id: id, page_size: 10 });
      sections.push(h("h3", {}, "Historial de precios"), simpleTable([
        { title: "Fecha", render: (r) => dateTime(r.changed_at) }, { title: "Anterior", num: true, render: (r) => money(r.old_price) },
        { title: "Nuevo", num: true, render: (r) => money(r.new_price) }, { title: "Origen", key: "source" },
        { title: "Justificación", key: "justification" }, { title: "Usuario", key: "changed_by_name" }], hist.items, { emptyText: "Sin cambios de precio" }));
    }
    if (can("mercado.ver")) {
      const refs = await api.get("/mercado/referencias", { product_id: id });
      sections.push(h("h3", {}, "Referencias de mercado"), simpleTable([
        { title: "Producto fuente", key: "market_product_name" }, { title: "Mercado", key: "market" },
        { title: "Fecha", key: "price_date" }, { title: "Precio por unidad base", num: true, render: (r) => money(r.price_per_base_unit) },
        { title: "Estado", render: (r) => (r.usable ? h("span", { class: "badge ok" }, "utilizable") : h("span", { class: "badge warn" }, r.reason)) }],
      refs, { emptyText: "Sin correspondencia con la fuente de mercado" }));
    }
    clear(body, h("div", { class: "stack" }, sections));
  } catch (e) { clear(body, h("div", { class: "notice danger" }, e.message)); }
}

function presentationsTable(p) {
  return simpleTable([
    { title: "Presentación", render: (r) => h("span", {}, r.name, r.is_base ? h("span", { class: "badge info" }, " base") : null) },
    { title: "Unidad", key: "unit_code" },
    { title: "Contenido", num: true, render: (r) => qty(r.factor_to_base, p.base_unit_code) },
    { title: "Compra", render: (r) => (r.for_purchase ? "Sí" : "No") },
    { title: "Venta", render: (r) => (r.for_sale ? "Sí" : "No") },
    { title: "Estado", render: (r) => (r.is_active ? "Activa" : "Inactiva") },
    { title: "", actions: true, render: (r) => (can("productos.gestionar") && !r.is_base ? h("button", { class: "btn small ghost", onclick: async () => {
      try { await api.patch(`/productos/${p.id}/presentaciones/${r.id}`, { is_active: !r.is_active }); toast("Presentación actualizada"); } catch (e) { showError(e); }
    } }, r.is_active ? "Desactivar" : "Activar") : null) },
  ], p.presentations);
}

function addPresentationForm(p, done) {
  const form = h("form", { class: "form card", novalidate: true });
  units().then((us) => {
    form.append(h("h3", {}, "Agregar presentación"),
      h("div", { class: "cols" },
        field({ name: "unit_id", label: "Unidad", type: "select", required: true, options: [{ value: "", label: "Seleccione…" }, ...us.map((u) => ({ value: u.id, label: u.name }))] }),
        field({ name: "name", label: "Nombre", required: true, placeholder: "Ej.: Bulto 50 kg" }),
        field({ name: "factor_to_base", label: `Contenido en ${p.base_unit_code}`, type: "decimal", hint: "Obligatorio para cajas, bultos, canastillas… (se deduce solo entre kg, g y libra)" }),
        field({ name: "for_purchase", label: "Para compras", type: "checkbox", value: true }),
        field({ name: "for_sale", label: "Para ventas", type: "checkbox", value: true })),
      h("div", { class: "row end" }, h("button", { class: "btn primary", type: "submit" }, "Agregar")));
  });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    d.unit_id = Number(d.unit_id);
    try { await api.post(`/productos/${p.id}/presentaciones`, d); toast("Presentación agregada"); done(); } catch (err) { applyServerErrors(form, err); }
  };
  return form;
}
