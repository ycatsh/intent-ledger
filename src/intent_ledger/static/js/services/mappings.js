const TABLE_LABELS = {
  accounts: "account",
  payees: "payee",
  payee_aliases: "alias",
  counterparties: "counterparty",
};

const pendingChanges = new Map();

async function postJSON(url, body) {
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
    body: JSON.stringify(body),
  });
  return response.json();
}

function fieldValue(el) {
  if (el.type === "checkbox") return el.checked ? 1 : 0;
  if (el.type === "number") return Number(el.value);
  return el.value;
}

function describeChange(change) {
  const label = TABLE_LABELS[change.table] || change.table;
  const name = change.rowLabel
    || change.fields?.name || change.fields?.canonical_name || change.fields?.alias
    || "entry";

  if (change.op === "insert") return `add ${label} ${name}`;
  if (change.op === "delete") return `delete ${label} ${name}`;

  const fields = Object.keys(change.fields).filter((field) => !field.startsWith("normalized_"));
  return `edit ${label} ${name} (${fields.join(", ")})`;
}

function renderChangelog() {
  const bar = document.getElementById("mappings-changelog");
  const countEl = document.getElementById("changelog-count");
  const summaryEl = document.getElementById("changelog-summary");
  const commitBtn = document.getElementById("changelog-commit");
  const discardBtn = document.getElementById("changelog-discard");
  if (!bar || !countEl || !summaryEl) return;

  const changes = [...pendingChanges.values()];
  countEl.textContent = String(changes.length);
  bar.classList.toggle("hidden", changes.length === 0);
  if (commitBtn) commitBtn.disabled = changes.length === 0;
  if (discardBtn) discardBtn.disabled = changes.length === 0;

  summaryEl.textContent = "";
  for (const change of changes) {
    const row = document.createElement("div");
    row.textContent = describeChange(change);
    summaryEl.appendChild(row);
  }
}

function undoChange(key) {
  const change = pendingChanges.get(key);
  if (!change) return;

  pendingChanges.delete(key);

  if (change.op === "delete") {
    const button = document.querySelector(`[data-mapping-delete="${change.table}:${change.id}"]`);
    const row = button?.closest("tr");
    row?.classList.remove("opacity-50");
    row?.querySelectorAll("input, select").forEach((el) => { el.disabled = false; });
  }

  renderChangelog();
}

document.querySelectorAll("[data-mapping-insert]").forEach((button) => {
  button.addEventListener("click", () => {
    const table = button.dataset.mappingInsert;
    const row = button.closest("[data-new-row]");
    const fields = {};

    for (const el of row.querySelectorAll("[name]")) {
      if (el.type === "checkbox") {
        fields[el.name] = fieldValue(el);
        continue;
      }
      if (el.value === "") {
        if (el.required) {
          el.reportValidity();
          return;
        }
        continue;
      }
      fields[el.name] = fieldValue(el);
    }

    const tempId = crypto.randomUUID();
    pendingChanges.set(`${table}:${tempId}:insert`, { op: "insert", table, temp_id: tempId, fields });

    row.classList.add("hidden");
    row.querySelectorAll("input, select").forEach((el) => {
      if (el.type === "checkbox") el.checked = false;
      else if (el.tagName === "SELECT") el.selectedIndex = 0;
      else if (el.type !== "hidden" && el.dataset.comboboxDisplay === undefined) el.value = "";
    });
    Combobox.sync(row);

    renderChangelog();
  });
});

document.querySelectorAll("[data-mapping-update]").forEach((el) => {
  el.addEventListener("change", () => {
    const [table, id, field] = el.dataset.mappingUpdate.split(":");
    const row = el.closest("tr");
    const key = `${table}:${id}:update`;
    const existing = pendingChanges.get(key);

    if (existing) {
      existing.fields[field] = fieldValue(el);
    } else {
      pendingChanges.set(key, {
        op: "update",
        table,
        id: Number(id),
        fields: { [field]: fieldValue(el) },
        rowLabel: row?.dataset.rowLabel,
      });
    }

    renderChangelog();
  });
});

document.querySelectorAll("[data-mapping-delete]").forEach((button) => {
  button.addEventListener("click", () => {
    const [table, id] = button.dataset.mappingDelete.split(":");
    const key = `${table}:${id}:delete`;

    if (pendingChanges.has(key)) {
      undoChange(key);
      return;
    }

    if (!window.confirm("Delete this row?")) return;

    const row = button.closest("tr");
    pendingChanges.delete(`${table}:${id}:update`);
    pendingChanges.set(key, { op: "delete", table, id: Number(id), rowLabel: row?.dataset.rowLabel });

    row?.classList.add("opacity-50");
    row?.querySelectorAll("input, select").forEach((el) => { el.disabled = true; });

    renderChangelog();
  });
});

document.getElementById("changelog-toggle")?.addEventListener("click", () => {
  document.getElementById("changelog-summary")?.classList.toggle("hidden");
  document.getElementById("changelog-toggle-icon")?.classList.toggle("rotate-90");
});

document.getElementById("changelog-discard")?.addEventListener("click", () => {
  if (pendingChanges.size === 0) return;
  if (!window.confirm("Discard all pending changes?")) return;
  pendingChanges.clear();
  window.location.reload();
});

document.getElementById("changelog-commit")?.addEventListener("click", async () => {
  if (pendingChanges.size === 0) return;

  const changes = [...pendingChanges.values()].map(({ op, table, id, temp_id, fields }) => (
    { op, table, id, temp_id, fields }
  ));

  const data = await postJSON("/mappings/save", { changes });

  if (!data.ok) {
    window.alert(data.error || "Save failed.");
    return;
  }

  pendingChanges.clear();
  window.location.reload();
});

renderChangelog();

// Changes only exist in the browser until they are committed, and every tab
// on this page shares one changelog - so leaving loses all of them at once.
window.addEventListener("beforeunload", (event) => {
  if (pendingChanges.size === 0) return;
  event.preventDefault();
  event.returnValue = "";
});

document.getElementById("mappings-search")?.addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();

  document.querySelectorAll("[data-row-label]").forEach((row) => {
    const match = row.textContent.toLowerCase().includes(query);
    row.classList.toggle("hidden", !match);
  });

  // Every tab is filtered at once, so the tab counts become the only way to
  // see which one the matches are actually on.
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    const count = document.querySelector(`[data-tab-count="${panel.dataset.tabPanel}"]`);
    if (count) {
      count.textContent = String(panel.querySelectorAll("[data-row-label]:not(.hidden)").length);
    }
  });
});
