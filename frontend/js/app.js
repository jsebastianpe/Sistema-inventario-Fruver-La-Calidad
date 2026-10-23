import { api, setCsrf, setUnauthorizedHandler } from "./api.js";
import { applyServerErrors, clear, field, h, loadingEl, readForm, showError, submitting, toast } from "./ui.js";

// Menú: el servidor es quien autoriza; ocultar opciones solo mejora la experiencia.
const ROUTES = [
  { path: "inicio", label: "Inicio", icon: "🏠", perm: "productos.ver", load: () => import("./views/dashboard.js") },
  { path: "productos", label: "Productos", icon: "🥕", perm: "productos.ver", load: () => import("./views/products.js") },
  { path: "inventario", label: "Inventario", icon: "📦", perm: "inventario.ver", load: () => import("./views/inventory.js") },
  { path: "compras", label: "Compras", icon: "🧾", perm: "compras.ver", load: () => import("./views/purchases.js") },
  { path: "ventas", label: "Ventas", icon: "🛒", perm: "ventas.ver", load: () => import("./views/sales.js") },
  { path: "proveedores", label: "Proveedores", icon: "🚚", perm: "proveedores.ver", load: () => import("./views/suppliers.js") },
  { path: "mercado", label: "Precios de mercado", icon: "📈", perm: "mercado.ver", load: () => import("./views/market.js") },
  { path: "precios", label: "Precios sugeridos", icon: "🏷️", perm: "precios.ver", load: () => import("./views/pricing.js") },
  { path: "reportes", label: "Reportes", icon: "📊", perm: "reportes.operativos", load: () => import("./views/reports.js") },
  { path: "administracion", label: "Usuarios y configuración", icon: "⚙️", perm: null, load: () => import("./views/admin.js") },
];

const root = document.getElementById("app");
export const session = { me: null, perms: new Set() };
export const can = (p) => session.perms.has(p);

function setSession(me) {
  session.me = me;
  session.perms = new Set(me.permisos);
  setCsrf(me.csrf_token);
}

setUnauthorizedHandler(() => {
  if (session.me) {
    session.me = null;
    toast("Su sesión expiró. Inicie sesión de nuevo.", "warn");
  }
  renderLogin();
});

// ------------------------------------------------------------------ login

function renderLogin(message) {
  const form = h("form", { class: "form", novalidate: true },
    field({ name: "username", label: "Usuario", required: true, autocomplete: "username" }),
    field({ name: "password", label: "Contraseña", type: "password", required: true, autocomplete: "current-password" }));
  const btn = h("button", { class: "btn primary", type: "submit" }, "Ingresar");
  form.append(btn);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const data = readForm(form);
    if (!data) return;
    try {
      const me = await submitting(btn, () => api.post("/auth/login", data));
      setSession(me);
      location.hash = "#/inicio";
      renderShell();
    } catch (err) { applyServerErrors(form, err); }
  };
  clear(root, h("main", { class: "login-wrap" }, h("div", { class: "card login-card" },
    h("div", { class: "brand" }, h("span", { class: "brand-logo", "aria-hidden": "true" }, "F"), "Gestión Comercial"),
    message ? h("div", { class: "notice info" }, message) : null,
    h("h1", { class: "sr-only" }, "Iniciar sesión"), form,
    h("p", { class: "muted small" }, "¿Olvidó su contraseña? Pida al propietario que le asigne una contraseña temporal."))));
  form.querySelector("input")?.focus();
}

export function renderChangePassword(container, { forced = false } = {}) {
  const form = h("form", { class: "form", novalidate: true },
    field({ name: "current_password", label: "Contraseña actual", type: "password", required: true, autocomplete: "current-password" }),
    field({ name: "new_password", label: "Nueva contraseña", type: "password", required: true, autocomplete: "new-password",
      hint: "Mínimo 10 caracteres, combinando letras con números o símbolos." }),
    field({ name: "confirm", label: "Repita la nueva contraseña", type: "password", required: true, autocomplete: "new-password" }));
  const btn = h("button", { class: "btn primary", type: "submit" }, "Cambiar contraseña");
  form.append(h("div", { class: "row end" }, btn));
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    if (d.new_password !== d.confirm) { applyServerErrors(form, { status: 422, details: [{ campo: "confirm", mensaje: "No coincide" }] }); return; }
    try {
      await submitting(btn, () => api.post("/auth/cambiar-contrasena", { current_password: d.current_password, new_password: d.new_password }));
      toast("Contraseña actualizada. Se cerraron sus otras sesiones.");
      setSession(await api.get("/auth/me"));
      if (forced) { location.hash = "#/inicio"; renderShell(); } else form.reset();
    } catch (err) { applyServerErrors(form, err); }
  };
  clear(container, h("div", { class: "card login-card" },
    h("h2", {}, forced ? "Debe cambiar su contraseña temporal" : "Cambiar mi contraseña"), form));
}

// ------------------------------------------------------------------ estructura principal

let main, nav, sidebar;

function renderShell() {
  const me = session.me.usuario;
  sidebar = h("nav", { class: "sidebar", "aria-label": "Menú principal" },
    h("div", { class: "brand" }, h("span", { class: "brand-logo", "aria-hidden": "true" }, "F"), "Gestión Comercial"));
  nav = h("ul", { class: "nav" });
  for (const r of ROUTES) {
    if (r.perm && !can(r.perm)) continue;
    nav.append(h("li", {}, h("a", { href: `#/${r.path}`, dataset: { path: r.path }, onclick: () => sidebar.classList.remove("open") },
      h("span", { "aria-hidden": "true" }, r.icon), r.label)));
  }
  sidebar.append(nav);
  main = h("main", { id: "contenido", tabindex: "-1" });
  const top = h("header", { class: "topbar" },
    h("button", { class: "btn menu-btn", type: "button", "aria-label": "Abrir menú", onclick: () => sidebar.classList.toggle("open") }, "☰"),
    h("div", { class: "spacer" }),
    h("div", { class: "userbox" },
      h("span", { class: "name" }, me.full_name), h("span", { class: "badge" }, me.role),
      h("button", { class: "btn small", type: "button", onclick: logout }, "Cerrar sesión")));
  clear(root, h("div", { class: "layout" }, sidebar, h("div", { class: "main" }, top, main)));
  route();
}

async function logout() {
  try { await api.post("/auth/logout"); } catch { /* la sesión ya no es válida */ }
  session.me = null;
  renderLogin("Sesión cerrada.");
}

let routeSeq = 0;

async function route() {
  if (!session.me) return;
  if (session.me.usuario.must_change_password) {
    renderChangePassword(main, { forced: true });
    return;
  }
  const path = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "inicio";
  const r = ROUTES.find((x) => x.path === path && (!x.perm || can(x.perm))) || ROUTES[0];
  nav.querySelectorAll("a").forEach((a) => { if (a.dataset.path === r.path) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  document.title = `${r.label} · Gestión Comercial`;
  clear(main, loadingEl());
  const seq = ++routeSeq;  // una navegación posterior invalida esta (no debe pintar ni robar el foco)
  try {
    const mod = await r.load();
    if (seq !== routeSeq) return;
    clear(main);
    await mod.render(main);
    if (seq === routeSeq) main.focus({ preventScroll: true });
  } catch (err) {
    clear(main, h("div", { class: "notice danger" }, "No se pudo cargar la sección. ", err.message || ""));
    showError(err);
  }
}

window.addEventListener("hashchange", route);

(async function boot() {
  try {
    setSession(await api.get("/auth/me"));
    renderShell();
  } catch (err) {
    if (err.status !== 401) showError(err);
    renderLogin();
  }
})();
