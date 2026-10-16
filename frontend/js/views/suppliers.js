import { api } from "../api.js";
import { can } from "../app.js";
import { DataTable, applyServerErrors, field, h, modal, readForm, submitting, toast } from "../ui.js";
import { pageHeader } from "./common.js";

let table;

export async function render(el) {
  table = new DataTable({
    searchPlaceholder: "Buscar por nombre, NIT o contacto…",
    filters: [h("select", { name: "activo", "aria-label": "Estado" }, h("option", { value: "true" }, "Activos"), h("option", { value: "false" }, "Inactivos"))],
    load: (p) => api.get("/proveedores", p),
    rowClass: (r) => (r.is_active ? "" : "inactive"),
    columns: [
      { title: "Proveedor", render: (r) => h("strong", {}, r.name) }, { title: "NIT / cédula", key: "tax_id" },
      { title: "Contacto", key: "contact_name" }, { title: "Teléfono", key: "phone" }, { title: "Correo", key: "email" },
      { title: "", actions: true, render: (r) => (can("proveedores.gestionar") ? h("button", { class: "btn small", onclick: () => edit(r) }, "Editar") : null) },
    ],
  });
  el.append(pageHeader("Proveedores", can("proveedores.gestionar") ? h("button", { class: "btn primary", onclick: () => edit() }, "+ Nuevo proveedor") : null), table.el);
  await table.refresh();
}

function edit(s = null) {
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "cols" },
      field({ name: "name", label: "Nombre comercial o razón social", required: true, value: s?.name }),
      field({ name: "tax_id", label: "NIT o cédula", value: s?.tax_id, hint: "Solo números, con dígito de verificación opcional: 900123456-7" }),
      field({ name: "contact_name", label: "Persona de contacto", value: s?.contact_name }),
      field({ name: "phone", label: "Teléfono", value: s?.phone }),
      field({ name: "email", label: "Correo", type: "email", value: s?.email }),
      field({ name: "address", label: "Dirección", value: s?.address })),
    field({ name: "notes", label: "Observaciones", type: "textarea", value: s?.notes }),
    s ? field({ name: "is_active", label: "Activo", type: "checkbox", value: s.is_active }) : null);
  const save = h("button", { class: "btn primary", type: "submit" }, "Guardar");
  form.append(h("div", { class: "row end" }, save));
  const m = modal({ title: s ? `Editar: ${s.name}` : "Nuevo proveedor", body: form });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form);
    if (!d) return;
    try {
      await submitting(save, () => (s ? api.patch(`/proveedores/${s.id}`, d) : api.post("/proveedores", d)));
      toast("Proveedor guardado");
      m.close(); table.refresh();
    } catch (err) { applyServerErrors(form, err); }
  };
}
