// Formatos para Colombia: COP, separador de miles ".", decimal ",", zona horaria America/Bogota.
// Solo se usan para MOSTRAR; los cálculos los hace el servidor con decimales exactos.
const TZ = "America/Bogota";
const copFmt = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", minimumFractionDigits: 0, maximumFractionDigits: 2 });
const qtyFmt = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 4 });
const numFmt = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 });
const dtFmt = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" });
const dFmt = new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", dateStyle: "medium" });

const empty = (v) => v === null || v === undefined || v === "";

export const money = (v) => (empty(v) ? "—" : copFmt.format(Number(v)));
export const qty = (v, unit) => (empty(v) ? "—" : `${qtyFmt.format(Number(v))}${unit ? " " + unit : ""}`);
export const num = (v) => (empty(v) ? "—" : numFmt.format(Number(v)));
export const pct = (v) => (empty(v) ? "—" : `${numFmt.format(Number(v) * 100)} %`);
export const signedPct = (v) => (empty(v) ? "—" : `${Number(v) > 0 ? "+" : ""}${numFmt.format(Number(v) * 100)} %`);

export function dateTime(v) {
  return empty(v) ? "—" : dtFmt.format(new Date(v));
}

export function date(v) {
  if (empty(v)) return "—";
  // "AAAA-MM-DD" es una fecha calendario: se formatea sin conversión de zona.
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return dFmt.format(new Date(`${v}T00:00:00Z`));
  return dFmt.format(new Date(v));
}

export function todayISO() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  return parts; // AAAA-MM-DD
}

export function monthStartISO() {
  return todayISO().slice(0, 8) + "01";
}

/**
 * Interpreta un número escrito en formato colombiano o internacional y lo devuelve como texto
 * con punto decimal (lo que espera la API), o null si no es válido.
 *   "1.234,5" → "1234.5"   "1234,5" → "1234.5"   "2.5" → "2.5"   "1.500" → "1500"
 */
export function parseDecimal(input) {
  if (input === null || input === undefined) return null;
  let s = String(input).trim().replace(/[\s$]/g, "");
  if (s === "") return null;
  if (s.includes(",")) {
    s = s.replace(/\./g, "").replace(",", ".");
  } else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) {
    s = s.replace(/\./g, "");
  }
  return /^-?\d+(\.\d+)?$/.test(s) ? s : null;
}

export const MOVEMENT_LABELS = {
  inicial: "Existencia inicial", compra: "Compra", venta: "Venta", devolucion_cliente: "Devolución de cliente",
  devolucion_proveedor: "Devolución a proveedor", merma: "Merma / deterioro", ajuste_entrada: "Ajuste de entrada",
  ajuste_salida: "Ajuste de salida", anulacion_compra: "Anulación de compra", anulacion_venta: "Anulación de venta",
  reversion: "Reversión",
};

export const STATUS_LABELS = {
  borrador: "Borrador", confirmada: "Confirmada", anulada: "Anulada", pendiente: "Pendiente",
  aprobada: "Aprobada", rechazada: "Rechazada", reemplazada: "Reemplazada", exito: "Éxito",
  sin_datos_nuevos: "Sin datos nuevos", error: "Error", error_transitorio: "Error transitorio", en_curso: "En curso",
};
