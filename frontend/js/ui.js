// Componentes de interfaz. Los nodos se crean con textContent (nunca innerHTML con datos) para evitar XSS.
import { parseDecimal, num, STATUS_LABELS } from "./format.js";

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export function toast(message, type = "ok", ms = 4500) {
  const t = h("div", { class: `toast ${type}`, role: type === "error" ? "alert" : "status" }, message);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

export function errorText(err) {
  if (!err) return "Error desconocido";
  let msg = err.message || String(err);
  if (Array.isArray(err.details) && err.details.length && typeof err.details[0] === "object" && err.details[0].mensaje) {
    msg += ": " + err.details.map((d) => (d.campo ? `${d.campo}: ${d.mensaje}` : d.mensaje)).join("; ");
  }
  return msg;
}

export function showError(err) {
  toast(errorText(err), "error", 8000);
}

export const loadingEl = (text = "Cargando…") => h("div", { class: "loading" }, h("span", { class: "spinner" }), " ", text);

export function badge(status, map = {}) {
  const cls = map[status] || { confirmada: "ok", aprobada: "ok", exito: "ok", borrador: "info", pendiente: "warn",
    sin_datos_nuevos: "info", anulada: "danger", rechazada: "danger", error: "danger", error_transitorio: "warn" }[status] || "";
  return h("span", { class: `badge ${cls}` }, STATUS_LABELS[status] || status);
}

// ------------------------------------------------------------------ diálogos

export function modal({ title, body, actions = [], wide = false, onClose }) {
  const dlg = h("dialog", { class: wide ? "wide" : "", "aria-labelledby": "dlg-title" });
  const close = () => { dlg.close(); };
  dlg.addEventListener("close", () => { dlg.remove(); onClose?.(); });
  dlg.append(
    h("div", { class: "dlg-head" }, h("h2", { id: "dlg-title" }, title),
      h("button", { class: "btn ghost", type: "button", "aria-label": "Cerrar", onclick: close }, "✕")),
    h("div", { class: "dlg-body" }, body),
    actions.length ? h("div", { class: "dlg-foot" }, actions) : null,
  );
  document.body.append(dlg);
  dlg.showModal();
  return { dlg, close };
}

/** Confirmación para operaciones sensibles. Con `input`, exige un texto (p. ej. el motivo). */
export function confirmDialog({ title, message, confirmText = "Confirmar", danger = false, input = null }) {
  return new Promise((resolve) => {
    let result = null;
    const field = input ? h("textarea", { required: true, minlength: input.minLength || 3, "aria-label": input.label }) : null;
    const err = h("div", { class: "error small" });
    const ok = h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "button" }, confirmText);
    const cancel = h("button", { class: "btn", type: "button" }, "Cancelar");
    const m = modal({
      title,
      body: h("div", { class: "form" }, h("p", {}, message),
        input ? h("div", { class: "field" }, h("label", {}, input.label), field, err) : null),
      actions: [cancel, ok],
      onClose: () => resolve(result),
    });
    cancel.onclick = () => m.close();
    ok.onclick = () => {
      if (field) {
        const v = field.value.trim();
        if (v.length < (input.minLength || 3)) { err.textContent = `Escriba al menos ${input.minLength || 3} caracteres`; field.focus(); return; }
        result = v;
      } else result = true;
      m.close();
    };
    (field || ok).focus();
  });
}

// ------------------------------------------------------------------ formularios

/**
 * Campo de formulario. type: text|number|decimal|money|date|select|textarea|checkbox|password|email
 * decimal/money aceptan formato colombiano ("1.234,5") y muestran cómo se interpretó.
 */
export function field({ name, label, type = "text", value = "", required = false, options = [], hint = "",
  min, max, maxlength, pattern, placeholder, disabled = false, autocomplete }) {
  let input;
  if (type === "select") {
    input = h("select", { name, required, disabled },
      options.map((o) => h("option", { value: o.value, selected: String(o.value) === String(value ?? "") }, o.label)));
  } else if (type === "textarea") {
    input = h("textarea", { name, required, maxlength, placeholder, disabled }, value ?? "");
  } else if (type === "checkbox") {
    input = h("input", { type: "checkbox", name, checked: !!value, disabled });
    return h("div", { class: "field" }, h("label", { class: "check" }, input, label), hint ? h("div", { class: "hint" }, hint) : null);
  } else {
    const isDec = type === "decimal" || type === "money";
    input = h("input", {
      name, required, disabled, maxlength, pattern, placeholder, autocomplete, min, max,
      type: isDec ? "text" : type, inputmode: isDec ? "decimal" : null, value: value ?? "",
      dataset: isDec ? { decimal: "1" } : undefined,
    });
  }
  const hintEl = h("div", { class: "hint" }, hint);
  if (input.dataset?.decimal) {
    const show = () => {
      if (!input.value) { hintEl.textContent = hint; return; }
      const p = parseDecimal(input.value);
      hintEl.textContent = p === null ? "Número inválido" : `Se registrará: ${num(p)}${hint ? " — " + hint : ""}`;
    };
    input.addEventListener("input", show);
    show();
  }
  const id = `f-${name}-${Math.random().toString(36).slice(2, 7)}`;
  input.id = id;
  return h("div", { class: "field", dataset: { field: name } }, h("label", { for: id }, label, required ? " *" : ""), input, hintEl, h("div", { class: "error", role: "alert" }));
}

/** Lee el formulario validando lo básico (obligatorios y números). Devuelve null si hay errores. */
export function readForm(form) {
  clearErrors(form);
  const data = {};
  let ok = true;
  for (const el of form.querySelectorAll("input[name], select[name], textarea[name]")) {
    const name = el.name;
    if (el.type === "checkbox") { data[name] = el.checked; continue; }
    let v = el.value.trim();
    if (el.required && v === "") { setError(form, name, "Campo obligatorio"); ok = false; continue; }
    if (el.dataset.decimal && v !== "") {
      const p = parseDecimal(v);
      if (p === null) { setError(form, name, "Número inválido"); ok = false; continue; }
      v = p;
    }
    data[name] = v === "" ? null : v;
  }
  if (!ok) form.querySelector(".field.invalid input, .field.invalid select, .field.invalid textarea")?.focus();
  return ok ? data : null;
}

export function setError(form, name, msg) {
  const f = form.querySelector(`.field[data-field="${CSS.escape(name)}"]`);
  if (!f) return false;
  f.classList.add("invalid");
  f.querySelector(".error").textContent = msg;
  return true;
}

export function clearErrors(form) {
  form.querySelectorAll(".field.invalid").forEach((f) => { f.classList.remove("invalid"); f.querySelector(".error").textContent = ""; });
}

/** Muestra los errores de validación del servidor junto a cada campo; el resto como aviso. */
export function applyServerErrors(form, err) {
  let shown = false;
  if (Array.isArray(err.details)) {
    for (const d of err.details) {
      if (d && d.campo && setError(form, d.campo.split(".").pop(), d.mensaje)) shown = true;
    }
  }
  if (!shown || err.status !== 422) showError(err);
}

/** Envía un formulario deshabilitando el botón mientras tanto. */
export async function submitting(button, fn) {
  const label = button.textContent;
  button.disabled = true;
  button.replaceChildren(h("span", { class: "spinner" }), " Guardando…");
  try { return await fn(); } finally { button.disabled = false; button.textContent = label; }
}

// ------------------------------------------------------------------ tabla de datos

/**
 * Tabla con búsqueda, filtros, ordenamiento y paginación del lado del servidor.
 * columns: [{ key, title, render?(row), num?, sort? (clave de orden del servidor) }]
 * load: async ({ page, page_size, q, orden, ...filtros }) => { items, total, page, page_size }
 */
export class DataTable {
  constructor({ columns, load, search = true, searchPlaceholder = "Buscar…", filters = [], pageSize = 25,
    rowClass, emptyText = "No hay registros" }) {
    Object.assign(this, { columns, load, rowClass, emptyText });
    this.state = { page: 1, page_size: pageSize, q: "", orden: null };
    this.filterEls = filters;
    this.body = h("tbody");
    this.info = h("span", { class: "muted small" });
    this.prev = h("button", { class: "btn small", type: "button", onclick: () => this.go(-1) }, "‹ Anterior");
    this.next = h("button", { class: "btn small", type: "button", onclick: () => this.go(1) }, "Siguiente ›");
    const head = h("tr", {}, columns.map((c) => {
      const th = h("th", { class: `${c.num ? "num" : ""} ${c.sort ? "sortable" : ""}`, scope: "col" }, c.title);
      if (c.sort) {
        th.tabIndex = 0;
        const toggle = () => {
          const asc = this.state.orden === c.sort;
          this.state.orden = asc && c.sortDesc ? c.sortDesc : c.sort;
          head.querySelectorAll("th").forEach((x) => x.removeAttribute("aria-sort"));
          th.setAttribute("aria-sort", this.state.orden === c.sort ? "ascending" : "descending");
          this.refresh(1);
        };
        th.onclick = toggle;
        th.onkeydown = (e) => { if (e.key === "Enter") toggle(); };
      }
      return th;
    }));
    let timer;
    this.searchInput = search ? h("input", { type: "search", placeholder: searchPlaceholder, "aria-label": "Buscar",
      oninput: (e) => { clearTimeout(timer); timer = setTimeout(() => { this.state.q = e.target.value.trim(); this.refresh(1); }, 300); } }) : null;
    for (const f of filters) f.addEventListener("change", () => this.refresh(1));
    this.el = h("div", {},
      (search || filters.length) ? h("div", { class: "toolbar" }, this.searchInput ? h("div", { class: "grow" }, this.searchInput) : null, filters) : null,
      h("div", { class: "table-wrap" }, h("table", { class: "responsive" }, h("thead", {}, head), this.body)),
      h("div", { class: "pager" }, this.info, this.prev, this.next));
  }

  filters() {
    const out = {};
    for (const f of this.filterEls) {
      const el = f.matches("select, input") ? f : f.querySelector("select, input");
      if (!el?.name) continue;
      if (el.type === "checkbox") { if (el.checked) out[el.name] = true; } else if (el.value !== "") out[el.name] = el.value;
    }
    return out;
  }

  go(delta) { this.refresh(this.state.page + delta); }

  async refresh(page = this.state.page) {
    this.state.page = page;
    clear(this.body, h("tr", {}, h("td", { colspan: this.columns.length }, loadingEl())));
    try {
      const params = { page: this.state.page, page_size: this.state.page_size, ...this.filters() };
      if (this.state.q) params.q = this.state.q;
      if (this.state.orden) params.orden = this.state.orden;
      const res = await this.load(params);
      const items = res.items ?? res;
      clear(this.body, items.length ? items.map((row) => this.renderRow(row))
        : h("tr", {}, h("td", { colspan: this.columns.length, class: "empty" }, this.emptyText)));
      const total = res.total ?? items.length;
      const pages = Math.max(1, Math.ceil(total / this.state.page_size));
      this.info.textContent = `${total} registro${total === 1 ? "" : "s"} · página ${this.state.page} de ${pages}`;
      this.prev.disabled = this.state.page <= 1;
      this.next.disabled = this.state.page >= pages;
    } catch (e) {
      clear(this.body, h("tr", {}, h("td", { colspan: this.columns.length, class: "empty" }, `No se pudo cargar: ${errorText(e)}`)));
    }
  }

  renderRow(row) {
    return h("tr", { class: this.rowClass?.(row) || "" }, this.columns.map((c) => {
      const v = c.render ? c.render(row) : row[c.key];
      return h("td", { class: `${c.num ? "num" : ""} ${c.actions ? "actions" : ""}`, "data-label": c.title }, v ?? "—");
    }));
  }
}

/** Tabla simple a partir de un arreglo (sin paginación del servidor). */
export function simpleTable(columns, rows, { emptyText = "Sin datos", footer = null } = {}) {
  return h("div", { class: "table-wrap" }, h("table", { class: "responsive" },
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : "", scope: "col" }, c.title)))),
    h("tbody", {}, rows.length ? rows.map((r) => h("tr", {}, columns.map((c) =>
      h("td", { class: `${c.num ? "num" : ""} ${c.actions ? "actions" : ""}`, "data-label": c.title }, (c.render ? c.render(r) : r[c.key]) ?? "—"))))
      : h("tr", {}, h("td", { colspan: columns.length, class: "empty" }, emptyText))),
    footer ? h("tfoot", {}, footer) : null));
}

export function tabs(items, onSelect) {
  const bar = h("div", { class: "tabs", role: "tablist" });
  const buttons = items.map((it, i) => h("button", { type: "button", role: "tab", "aria-selected": i === 0 ? "true" : "false",
    onclick: () => { buttons.forEach((b) => b.setAttribute("aria-selected", "false")); buttons[i].setAttribute("aria-selected", "true"); onSelect(it.id); } }, it.label));
  bar.append(...buttons);
  return bar;
}

/** Gráfica de línea mínima en SVG (sin dependencias). points: [{x: etiqueta, y: número, alert?}] */
export function lineChart(points, { formatY = (v) => v } = {}) {
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
  const W = 600, H = 180, P = { l: 70, r: 10, t: 10, b: 24 };
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Gráfica de evolución" });
  if (points.length < 2) return h("p", { class: "muted" }, "No hay suficientes datos para graficar.");
  const ys = points.map((p) => p.y);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = (i) => P.l + (i * (W - P.l - P.r)) / (points.length - 1);
  const y = (v) => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
  svg.append(s("line", { class: "axis", x1: P.l, y1: H - P.b, x2: W - P.r, y2: H - P.b }));
  for (const v of [lo, (lo + hi) / 2, hi]) {
    const t = s("text", { x: P.l - 6, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = formatY(v);
    svg.append(t);
  }
  svg.append(s("polyline", { class: "line", points: points.map((p, i) => `${x(i)},${y(p.y)}`).join(" ") }));
  points.forEach((p, i) => { if (p.alert) svg.append(s("circle", { class: "dot-alert", cx: x(i), cy: y(p.y), r: 4 })); });
  for (const i of [0, points.length - 1]) {
    const t = s("text", { x: x(i), y: H - 6, "text-anchor": i === 0 ? "start" : "end" });
    t.textContent = points[i].x;
    svg.append(t);
  }
  return h("div", { class: "chart" }, svg);
}
