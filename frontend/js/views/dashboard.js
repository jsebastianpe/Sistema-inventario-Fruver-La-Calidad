import { api } from "../api.js";
import { can } from "../app.js";
import { date, dateTime, money, monthStartISO, num, qty, signedPct, todayISO } from "../format.js";
import { clear, h, loadingEl, simpleTable } from "../ui.js";
import { pageHeader } from "./common.js";

const kpi = (label, value, hint, cls = "") =>
  h("div", { class: `card kpi ${cls}` }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);

export async function render(el) {
  const desde = h("input", { type: "date", value: monthStartISO(), "aria-label": "Desde" });
  const hasta = h("input", { type: "date", value: todayISO(), "aria-label": "Hasta" });
  const body = h("div", {}, loadingEl());
  const load = async () => {
    clear(body, loadingEl());
    const [d, alerts] = await Promise.all([api.get("/panel", { desde: desde.value, hasta: hasta.value }), api.get("/alertas")]);
    clear(body, view(d, alerts));
  };
  desde.onchange = hasta.onchange = load;
  el.append(pageHeader("Inicio", h("label", { class: "small muted" }, "Período "), desde, hasta), body);
  await load();
}

function view(d, alerts) {
  const kpis = [
    kpi("Productos activos", num(d.productos_activos)),
    kpi("Existencias bajas", num(d.bajo_minimo.total), "en o por debajo del mínimo", d.bajo_minimo.total ? "warn" : ""),
    kpi("Próximos a vencer", num(d.por_vencer.total), `en ${d.por_vencer.dias_alerta} días o vencidos`, d.por_vencer.total ? "warn" : ""),
  ];
  if ("valor_inventario_estimado" in d) kpis.push(kpi("Valor del inventario", money(d.valor_inventario_estimado), "estimado al costo promedio"));
  if (d.ventas) {
    kpis.push(kpi("Ventas del período", money(d.ventas.total), `${num(d.ventas.cantidad)} ventas`));
    if ("margen_bruto_estimado" in d.ventas) kpis.push(kpi("Margen bruto estimado", money(d.ventas.margen_bruto_estimado), "no es utilidad neta"));
  }
  if (d.compras) kpis.push(kpi("Compras del período", money(d.compras.total), `${num(d.compras.cantidad)} compras confirmadas`));
  if ("sugerencias_pendientes" in d) kpis.push(kpi("Sugerencias de precio", num(d.sugerencias_pendientes), "pendientes de revisión", d.sugerencias_pendientes ? "warn" : ""));

  const act = d.actualizacion;
  const sync = act ? h("div", { class: "card" }, h("h2", {}, "Actualización de precios de mercado"),
    h("p", {}, h("span", { class: `badge ${act.estado === "exito" || act.estado === "sin_datos_nuevos" ? "ok" : act.estado ? "danger" : ""}` },
      act.estado ? act.estado.replaceAll("_", " ") : "sin ejecuciones"),
    " ", act.proceso_activo ? h("span", { class: "badge ok" }, "proceso automático activo") : h("span", { class: "badge danger" }, "proceso automático detenido")),
    h("p", { class: "small" }, "Último éxito: ", dateTime(act.ultimo_exito), " · datos hasta: ", date(act.ultima_fecha_datos)),
    act.error ? h("div", { class: "notice danger small" }, act.error) : null,
    h("p", { class: "muted small" }, act.fuente)) : null;

  const levels = { critica: "danger", advertencia: "warn", info: "info" };
  return h("div", { class: "stack" },
    h("div", { class: "grid kpis" }, kpis),
    alerts.length ? h("div", { class: "card" }, h("h2", {}, "Alertas"),
      alerts.map((a) => h("div", { class: `notice ${levels[a.nivel] || "info"} small` }, a.mensaje))) : null,
    h("div", { class: "grid two" },
      h("div", { class: "card" }, h("h2", {}, "Existencias bajas"),
        simpleTable([{ title: "Producto", key: "producto" }, { title: "Existencia", num: true, render: (r) => qty(r.existencia, r.unidad) },
          { title: "Mínimo", num: true, render: (r) => qty(r.minimo, r.unidad) }], d.bajo_minimo.items, { emptyText: "Ningún producto bajo el mínimo" })),
      h("div", { class: "card" }, h("h2", {}, "Próximos a vencer"),
        simpleTable([{ title: "Producto", key: "producto" }, { title: "Vence", render: (r) => date(r.vence) },
          { title: "Días", num: true, render: (r) => (r.dias < 0 ? h("span", { class: "badge danger" }, "vencido") : num(r.dias)) },
          { title: "Cantidad", num: true, render: (r) => qty(r.cantidad, r.unidad) }], d.por_vencer.items, { emptyText: "Sin lotes próximos a vencer" })),
      h("div", { class: "card" }, h("h2", {}, "Productos con mayor movimiento"),
        simpleTable([{ title: "Producto", key: "producto" }, { title: "Vendido", num: true, render: (r) => qty(r.cantidad, r.unidad) }],
          d.mayor_movimiento, { emptyText: "Sin ventas en el período" })),
      can("mercado.ver") ? h("div", { class: "card" }, h("h2", {}, "Cambios de precios de mercado"),
        h("p", { class: "muted small" }, "Variaciones extraordinarias de los últimos 7 días en productos con correspondencia."),
        simpleTable([{ title: "Producto (fuente)", key: "producto_fuente" }, { title: "Mercado", key: "mercado" },
          { title: "Fecha", render: (r) => date(r.fecha) }, { title: "Precio", num: true, render: (r) => money(r.precio) },
          { title: "Variación", num: true, render: (r) => signedPct(r.variacion) }], d.cambios_mercado || [], { emptyText: "Sin variaciones extraordinarias" })) : null,
      sync));
}
