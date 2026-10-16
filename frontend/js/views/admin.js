import { api } from "../api.js";
import { can, renderChangePassword, session } from "../app.js";
import { dateTime, pct } from "../format.js";
import {
  applyServerErrors, clear, confirmDialog, field, h, loadingEl, modal, readForm, showError, simpleTable, submitting, tabs, toast,
} from "../ui.js";
import { categories, invalidate, pageHeader, units } from "./common.js";

const SETTINGS = [
  ["Precios", [
    ["default_margin", "Margen bruto objetivo general (0 a 0,99)", "decimal"],
    ["sales_commission_rate", "Comisión o costo variable sobre el precio (0 a 0,99)", "decimal"],
    ["rounding_step", "Redondeo comercial: múltiplo de (COP)", "decimal"],
    ["rounding_mode", "Dirección del redondeo", "select", [["arriba", "Hacia arriba"], ["cercano", "Al más cercano"], ["abajo", "Hacia abajo"]]],
    ["price_change_alert_rate", "Alertar cambios de precio mayores a (0,15 = 15 %)", "decimal"],
  ]],
  ["Política automática de precios", [
    ["auto_price_update_enabled", "Permitir aplicar sugerencias automáticamente (sin advertencias y dentro del límite)", "checkbox"],
    ["auto_price_max_change_rate", "Cambio máximo que se aplica solo (0,10 = 10 %)", "decimal"],
  ]],
  ["Compras, inventario y ventas", [
    ["purchase_tax_in_cost", "El IVA de las compras forma parte del costo (no es descontable)", "checkbox"],
    ["allow_negative_stock", "Permitir existencias negativas", "checkbox"],
    ["expiry_alert_days", "Alertar vencimientos con anticipación de (días)", "number"],
    ["employee_max_discount_rate", "Descuento máximo sin autorización (0,05 = 5 %)", "decimal"],
  ]],
  ["Mercado", [
    ["market_sync_enabled", "Actualizar precios de mercado automáticamente", "checkbox"],
    ["stale_quote_days", "Considerar desactualizada una cotización después de (días)", "number"],
    ["market_variation_alert_rate", "Variación extraordinaria de mercado mayor a (0,20 = 20 %)", "decimal"],
  ]],
];

export async function render(el) {
  const content = h("div");
  const items = [{ id: "cuenta", label: "Mi cuenta" }];
  if (can("usuarios.gestionar")) items.push({ id: "usuarios", label: "Usuarios y permisos" });
  if (can("precios.ver")) items.push({ id: "config", label: "Configuración comercial" });
  if (can("productos.gestionar")) items.push({ id: "categorias", label: "Categorías" });
  items.push({ id: "unidades", label: "Unidades" });
  if (can("auditoria.ver")) items.push({ id: "auditoria", label: "Auditoría" });
  const show = (id) => { clear(content); ({ cuenta: account, usuarios: users, config, categorias: cats, unidades: unitsView, auditoria: audit })[id](content); };
  el.append(pageHeader("Usuarios y configuración"), tabs(items, show), content);
  show("cuenta");
}

function account(el) {
  const u = session.me.usuario;
  el.append(h("div", { class: "card" }, h("h2", {}, u.full_name), h("p", {}, `Usuario: ${u.username} · Rol: ${u.role}`),
    h("p", { class: "muted small" }, `Permisos: ${session.me.permisos.join(", ")}`)));
  const box = h("div");
  el.append(box);
  renderChangePassword(box);
}

async function users(el) {
  el.append(loadingEl());
  const [list, roles] = await Promise.all([api.get("/usuarios"), api.get("/usuarios/roles")]);
  const reload = () => { clear(el); users(el); };
  clear(el, h("div", { class: "row end" }, h("button", { class: "btn primary", onclick: () => createUser(reload) }, "+ Nuevo usuario")),
    simpleTable([
      { title: "Nombre", key: "full_name" }, { title: "Usuario", key: "username" }, { title: "Rol", key: "role" },
      { title: "Estado", render: (u) => (u.is_active ? h("span", { class: "badge ok" }, "Activo") : h("span", { class: "badge danger" }, "Inactivo")) },
      { title: "Permisos extra", render: (u) => u.grants.join(", ") || "—" },
      { title: "Último acceso", render: (u) => dateTime(u.last_login_at) },
      { title: "", actions: true, render: (u) => h("span", {},
        h("button", { class: "btn small", onclick: () => editUser(u, roles, reload) }, "Editar"),
        h("button", { class: "btn small", onclick: () => resetPassword(u) }, "Contraseña temporal")) },
    ], list),
    h("details", { class: "card" }, h("summary", {}, "Permisos de cada rol"),
      Object.entries(roles.roles).map(([r, perms]) => h("p", { class: "small" }, h("strong", {}, r), ": ", perms.join(", ")))));
}

function createUser(done) {
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "cols" },
      field({ name: "full_name", label: "Nombre completo", required: true }),
      field({ name: "username", label: "Usuario", required: true, hint: "3 a 50 caracteres: letras, números, punto, guion" }),
      field({ name: "role", label: "Rol", type: "select", required: true, options: [{ value: "empleado", label: "Empleado" }, { value: "administrador", label: "Administrador" }, { value: "propietario", label: "Propietario" }] }),
      field({ name: "new_password", label: "Contraseña temporal", type: "password", required: true, autocomplete: "new-password", hint: "La persona deberá cambiarla al ingresar" })));
  const save = h("button", { class: "btn primary", type: "submit" }, "Crear usuario");
  form.append(h("div", { class: "row end" }, save));
  const m = modal({ title: "Nuevo usuario", body: form });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    try { await submitting(save, () => api.post("/usuarios", d)); toast("Usuario creado"); m.close(); done(); } catch (err) { applyServerErrors(form, err); }
  };
}

function editUser(u, roles, done) {
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "cols" },
      field({ name: "full_name", label: "Nombre completo", required: true, value: u.full_name }),
      field({ name: "role", label: "Rol", type: "select", value: u.role, options: [{ value: "empleado", label: "Empleado" }, { value: "administrador", label: "Administrador" }, { value: "propietario", label: "Propietario" }] }),
      field({ name: "is_active", label: "Activo", type: "checkbox", value: u.is_active })),
    u.role !== "propietario" ? h("fieldset", { class: "card" }, h("legend", {}, "Permisos adicionales (autorización expresa)"),
      roles.delegables.filter((p) => !roles.roles[u.role].includes(p)).map((p) =>
        h("label", { class: "check small" }, h("input", { type: "checkbox", name: `grant:${p}`, checked: u.grants.includes(p) }), p))) : null);
  const save = h("button", { class: "btn primary", type: "submit" }, "Guardar");
  form.append(h("div", { class: "row end" }, save));
  const m = modal({ title: `Editar: ${u.full_name}`, body: form, wide: true });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    const grants = Object.entries(d).filter(([k, v]) => k.startsWith("grant:") && v).map(([k]) => k.slice(6));
    if (d.is_active === false && u.is_active) {
      const ok = await confirmDialog({ title: "Desactivar usuario", message: `${u.full_name} perderá el acceso y se cerrarán sus sesiones.`, danger: true, confirmText: "Desactivar" });
      if (!ok) return;
    }
    try {
      await submitting(save, async () => {
        await api.patch(`/usuarios/${u.id}`, { full_name: d.full_name, role: d.role, is_active: d.is_active });
        if (u.role !== "propietario" && d.role !== "propietario") await api.put(`/usuarios/${u.id}/permisos`, { permisos: grants });
      });
      toast("Usuario actualizado"); m.close(); done();
    } catch (err) { applyServerErrors(form, err); }
  };
}

function resetPassword(u) {
  const form = h("form", { class: "form", novalidate: true },
    h("p", {}, `Se asignará una contraseña temporal a ${u.full_name}. Sus sesiones abiertas se cerrarán y deberá cambiarla al ingresar. Entréguela por un medio seguro.`),
    field({ name: "new_password", label: "Contraseña temporal", type: "password", required: true, autocomplete: "new-password" }));
  const save = h("button", { class: "btn danger", type: "submit" }, "Asignar contraseña temporal");
  form.append(h("div", { class: "row end" }, save));
  const m = modal({ title: "Recuperación de acceso", body: form });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    try { await submitting(save, () => api.post(`/usuarios/${u.id}/restablecer-contrasena`, d)); toast("Contraseña temporal asignada"); m.close(); } catch (err) { applyServerErrors(form, err); }
  };
}

async function config(el) {
  el.append(loadingEl());
  const s = await api.get("/configuracion");
  const editable = can("configuracion.gestionar");
  const form = h("form", { class: "form", novalidate: true },
    SETTINGS.map(([title, fields]) => h("fieldset", { class: "card" }, h("legend", {}, h("strong", {}, title)),
      h("div", { class: "cols" }, fields.map(([name, label, type, options]) => field({
        name, label, type: type === "number" ? "number" : type, value: s[name], disabled: !editable,
        options: options?.map(([v, l]) => ({ value: v, label: l })),
        hint: type === "decimal" && name.includes("rate") || name === "default_margin" ? `= ${pct(s[name])}` : "" }))))));
  if (editable) {
    const save = h("button", { class: "btn primary", type: "submit" }, "Guardar configuración");
    form.append(h("div", { class: "notice warn small" }, "La política automática solo aplica sugerencias sin advertencias, con precio previo y dentro del límite. El primer precio de un producto siempre es manual."),
      h("div", { class: "row end" }, save));
    form.onsubmit = async (e) => {
      e.preventDefault();
      const d = readForm(form);
      if (!d) return;
      for (const k of ["expiry_alert_days", "stale_quote_days"]) d[k] = Number(d[k]);
      if (d.auto_price_update_enabled && !s.auto_price_update_enabled) {
        const ok = await confirmDialog({ title: "Habilitar precios automáticos", danger: true, confirmText: "Habilitar",
          message: `Las sugerencias que cumplan las protecciones cambiarán el precio de venta sin aprobación manual (cambio máximo ${pct(d.auto_price_max_change_rate)}). Quedará registrado en el historial.` });
        if (!ok) return;
      }
      try { await submitting(save, () => api.patch("/configuracion", d)); toast("Configuración guardada"); } catch (err) { applyServerErrors(form, err); }
    };
  } else form.prepend(h("p", { class: "muted small" }, "Solo el propietario puede modificar la configuración."));
  clear(el, form);
}

async function cats(el) {
  const list = await categories(true);
  const form = h("form", { class: "form card", novalidate: true }, h("h3", {}, "Nueva categoría"),
    h("div", { class: "cols" }, field({ name: "name", label: "Nombre", required: true }),
      can("configuracion.gestionar") ? field({ name: "target_margin", label: "Margen objetivo (0 a 0,99)", type: "decimal" }) : null),
    h("div", { class: "row end" }, h("button", { class: "btn primary", type: "submit" }, "Crear")));
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    try { await api.post("/categorias", d); invalidate("categories"); toast("Categoría creada"); clear(el); cats(el); } catch (err) { applyServerErrors(form, err); }
  };
  el.append(simpleTable([{ title: "Categoría", key: "name" }, { title: "Margen objetivo", render: (c) => (c.target_margin ? pct(c.target_margin) : "General") },
    { title: "Estado", render: (c) => (c.is_active ? "Activa" : "Inactiva") },
    { title: "", actions: true, render: (c) => (can("configuracion.gestionar") ? h("button", { class: "btn small", onclick: async () => {
      const v = await confirmDialog({ title: `Margen de ${c.name}`, message: "Escriba el margen objetivo entre 0 y 0,99 (p. ej. 0,35).", input: { label: "Margen", minLength: 1 } });
      if (v === null) return;
      try { await api.patch(`/categorias/${c.id}`, { target_margin: v.replace(",", ".") }); invalidate("categories"); toast("Margen actualizado"); clear(el); cats(el); } catch (err) { showError(err); }
    } }, "Margen") : null) }], list), form);
}

async function unitsView(el) {
  const list = await units();
  el.append(h("p", { class: "muted small" }, "Las unidades de masa tienen equivalencia fija en kg. Cajas, bultos y canastillas NO: su contenido se define en cada presentación de producto."),
    simpleTable([{ title: "Código", key: "code" }, { title: "Nombre", key: "name" }, { title: "Tipo", key: "kind" },
      { title: "Equivale a (kg)", render: (u) => u.kg_equivalent ?? "Según presentación" }], list));
  if (can("configuracion.gestionar")) {
    const form = h("form", { class: "form card", novalidate: true }, h("h3", {}, "Nueva unidad"),
      h("div", { class: "cols" }, field({ name: "code", label: "Código", required: true, hint: "minúsculas, sin espacios" }),
        field({ name: "name", label: "Nombre", required: true }),
        field({ name: "kind", label: "Tipo", type: "select", options: [{ value: "empaque", label: "Empaque (contenido variable)" }, { value: "conteo", label: "Conteo" }, { value: "masa", label: "Masa" }] }),
        field({ name: "kg_equivalent", label: "Equivalencia en kg (solo masa)", type: "decimal" })),
      h("div", { class: "row end" }, h("button", { class: "btn primary", type: "submit" }, "Crear")));
    form.onsubmit = async (e) => {
      e.preventDefault();
      const d = readForm(form);
      if (!d) return;
      try { await api.post("/unidades", d); invalidate("units"); toast("Unidad creada"); clear(el); unitsView(el); } catch (err) { applyServerErrors(form, err); }
    };
    el.append(form);
  }
}

function audit(el) {
  el.append(h("p", {}, "Consulte la auditoría completa en ", h("a", { href: "#/reportes" }, "Reportes → Auditoría"), " (con filtros de fecha y exportación)."));
}
