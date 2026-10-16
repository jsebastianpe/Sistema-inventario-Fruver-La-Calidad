// Cliente de la API. Toda regla de negocio vive en el servidor; aquí solo se transporta.
let csrfToken = null;
let onUnauthorized = () => {};

export class ApiError extends Error {
  constructor(status, body) {
    const err = body?.error || {};
    super(err.mensaje || `Error ${status}`);
    this.status = status;
    this.code = err.codigo;
    this.details = err.detalles;
  }
}

export function setCsrf(token) { csrfToken = token; }
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export function newIdempotencyKey() {
  return crypto.randomUUID().replaceAll("-", "");
}

function qs(params) {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.append(k, v);
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function request(method, path, { body, params, idempotencyKey, raw = false } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  let res;
  try {
    res = await fetch(`/api/v1${path}${qs(params)}`, {
      method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, { error: { mensaje: "No hay conexión con el servidor. Verifique su internet e intente de nuevo." } });
  }
  if (raw && res.ok) return res;
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const e = new ApiError(res.status, data);
    if (res.status === 401 && !path.startsWith("/auth/login")) onUnauthorized(e);
    throw e;
  }
  return data;
}

export const api = {
  get: (p, params) => request("GET", p, { params }),
  post: (p, body, opts = {}) => request("POST", p, { body, ...opts }),
  put: (p, body) => request("PUT", p, { body }),
  patch: (p, body) => request("PATCH", p, { body }),
};

export async function download(path, params) {
  const res = await request("GET", path, { params, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const name = (cd.match(/filename="([^"]+)"/) || [])[1] || "reporte";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
