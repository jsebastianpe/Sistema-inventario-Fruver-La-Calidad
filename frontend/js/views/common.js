import { api } from "../api.js";
import { money, qty } from "../format.js";
import { clear, h } from "../ui.js";

const cache = {};

export async function categories(force = false) {
  if (force || !cache.categories) cache.categories = await api.get("/categorias");
  return cache.categories;
}

export async function units() {
  if (!cache.units) cache.units = await api.get("/unidades");
  return cache.units;
}

export async function suppliers() {
  const r = await api.get("/proveedores", { page_size: 200 });
  return r.items;
}

export function invalidate(key) { delete cache[key]; }

export const opt = (items, label = "name", empty = null) =>
  [...(empty !== null ? [{ value: "", label: empty }] : []), ...items.map((i) => ({ value: i.id, label: i[label] }))];

export function pageHeader(title, ...actions) {
  return h("div", { class: "row between" }, h("h1", {}, title), h("div", { class: "row" }, actions));
}

/**
 * Buscador de productos con teclado. onPick(product) recibe el producto completo (con presentaciones).
 */
export function productPicker({ onPick, placeholder = "Buscar producto por nombre, código o código de barras…", showPrice = false, activeOnly = true }) {
  const input = h("input", { type: "search", placeholder, "aria-label": "Buscar producto", autocomplete: "off" });
  const results = h("div", { class: "search-results hidden", role: "listbox" });
  let timer;
  const search = async () => {
    const q = input.value.trim();
    if (!q) { results.classList.add("hidden"); return; }
    const r = await api.get("/productos", { q, page_size: 10, activo: activeOnly ? true : undefined });
    clear(results, r.items.length ? r.items.map((p) => h("button", { type: "button", role: "option",
      onclick: () => { onPick(p); input.value = ""; results.classList.add("hidden"); input.focus(); } },
      h("span", {}, h("strong", {}, p.name), p.variety ? ` · ${p.variety}` : "", h("span", { class: "muted small" }, ` (${p.sku})`)),
      h("span", { class: "muted small nowrap" }, showPrice ? `${money(p.current_price)}/${p.base_unit_code} · ` : "",
        `exist. ${qty(p.stock, p.base_unit_code)}`)))
      : h("div", { class: "empty" }, "Sin resultados"));
    results.classList.remove("hidden");
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { results.querySelector("button")?.focus(); e.preventDefault(); }
    if (e.key === "Enter") { e.preventDefault(); results.querySelector("button")?.click(); }
  });
  results.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.target.nextElementSibling?.focus(); e.preventDefault(); }
    if (e.key === "ArrowUp") { (e.target.previousElementSibling || input).focus(); e.preventDefault(); }
  });
  return h("div", {}, input, results);
}

export const presentationOptions = (p, purpose) =>
  p.presentations.filter((x) => x.is_active && (!purpose || x[`for_${purpose}`]))
    .map((x) => ({ value: x.id, label: x.is_base ? x.name : `${x.name} (= ${qty(x.factor_to_base, p.base_unit_code)})` }));
