document.getElementById("nav-toggle")?.addEventListener("click", () => {
  const sidebar = document.getElementById("sidebar");
  const expanded = sidebar.classList.toggle("hidden") === false;
  document.getElementById("nav-toggle").setAttribute("aria-expanded", String(expanded));
});

document.querySelectorAll("[data-flash]").forEach((flash) => {
  const dismiss = () => flash.remove();
  flash.querySelector("[data-flash-dismiss]")?.addEventListener("click", dismiss);
  // An error you looked away from is an error you never saw, so those wait to
  // be dismissed by hand. Confirmations can time out.
  if (flash.dataset.flash !== "error") setTimeout(dismiss, 6000);
});

document.querySelectorAll("[data-confirm]").forEach((el) => {
  // A submit button never sees its form's submit event - that fires on the
  // form and bubbles past it - so those have to be caught on click.
  el.addEventListener(el.tagName === "FORM" ? "submit" : "click", (event) => {
    if (!window.confirm(el.dataset.confirm)) {
      event.preventDefault();
    }
  });
});

document.querySelectorAll("[data-modal-open]").forEach((trigger) => {
  trigger.addEventListener("click", () => {
    document.getElementById(trigger.dataset.modalOpen)?.showModal();
  });
});

document.querySelectorAll("dialog.modal").forEach((dialog) => {
  dialog.querySelectorAll("[data-modal-close]").forEach((btn) => {
    btn.addEventListener("click", () => dialog.close());
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});

document.querySelectorAll("[data-tabs]").forEach((group) => {
  function activate(id, { push = true } = {}) {
    const btn = group.querySelector(`[data-tab-target="${CSS.escape(id)}"]`);
    if (!btn) return false;

    group.querySelectorAll("[data-tab-target]").forEach((b) => b.classList.remove("tab-active"));
    btn.classList.add("tab-active");

    group.querySelectorAll("[data-tab-panel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.tabPanel !== id);
    });

    // Keep the tab in the URL so a save that reloads the page - or a shared
    // link - comes back to the tab the work was started on.
    if (push) {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", id);
      window.history.replaceState({}, "", url);
    }

    return true;
  }

  group.querySelectorAll("[data-tab-target]").forEach((btn) => {
    btn.addEventListener("click", () => activate(btn.dataset.tabTarget));
  });

  const requested = new URLSearchParams(window.location.search).get("tab");
  if (requested) activate(requested, { push: false });
});

// "/" jumps to the page's search box, the way every list view expects.
document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
  const active = document.activeElement;
  if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable)) return;

  const search = document.querySelector('input[type="search"]:not([disabled])');
  if (!search) return;
  event.preventDefault();
  search.focus();
  search.select();
});

document.querySelectorAll("[data-reveal]").forEach((trigger) => {
  trigger.addEventListener("click", () => {
    document.getElementById(trigger.dataset.reveal)?.classList.toggle("hidden");
    trigger.querySelector("[data-reveal-chevron]")?.classList.toggle("rotate-90");
  });
});

document.querySelectorAll("[data-reveal-active-row]").forEach((trigger) => {
  trigger.addEventListener("click", () => {
    document.querySelector("[data-tab-panel]:not(.hidden) [data-new-row]")?.classList.remove("hidden");
  });
});

document.querySelectorAll("[data-reveal-row]").forEach((trigger) => {
  trigger.addEventListener("click", () => {
    const row = trigger.closest("[data-new-row]");
    if (!row) return;
    row.classList.add("hidden");
    row.querySelectorAll("input, select").forEach((el) => {
      if (el.tagName === "SELECT") el.selectedIndex = 0;
      else if (el.dataset.comboboxDisplay === undefined) el.value = "";
    });
    Combobox.sync(row);
  });
});

// Rows that expand into several sibling rows (a split's legs, say), which the
// single-element [data-reveal] toggle can't address.
document.querySelectorAll("[data-reveal-group]").forEach((trigger) => {
  trigger.addEventListener("click", () => {
    const key = trigger.dataset.revealGroup;
    const rows = document.querySelectorAll(`[data-reveal-target="${key}"]`);
    const expanded = trigger.getAttribute("aria-expanded") !== "true";
    rows.forEach((row) => row.classList.toggle("hidden", !expanded));
    trigger.setAttribute("aria-expanded", String(expanded));
    trigger.querySelector("[data-reveal-chevron]")?.classList.toggle("rotate-90", expanded);
  });
});

// Projects and Counterparty are empty for most ledgers, so they start off and
// the choice is remembered per browser - the first load is the only one the
// default decides.
const COLUMN_PREF_KEY = "ledger-hidden-columns";
const COLUMNS_HIDDEN_BY_DEFAULT = ["projects", "counterparty"];

const columnMenu = document.querySelector("[data-column-menu]");
const ledgerTable = document.querySelector(".ledger-table");

if (columnMenu && ledgerTable) {
  let hidden;
  try {
    hidden = new Set(JSON.parse(window.localStorage.getItem(COLUMN_PREF_KEY)) || COLUMNS_HIDDEN_BY_DEFAULT);
  } catch {
    hidden = new Set(COLUMNS_HIDDEN_BY_DEFAULT);
  }

  const toggles = [...columnMenu.querySelectorAll("[data-column-toggle]")];

  function applyColumns() {
    ledgerTable.dataset.hidden = [...hidden].join(" ");
    toggles.forEach((toggle) => {
      toggle.checked = !hidden.has(toggle.dataset.columnToggle);
    });
  }

  toggles.forEach((toggle) => {
    toggle.addEventListener("change", () => {
      const column = toggle.dataset.columnToggle;
      toggle.checked ? hidden.delete(column) : hidden.add(column);
      try {
        window.localStorage.setItem(COLUMN_PREF_KEY, JSON.stringify([...hidden]));
      } catch {
        // A browser refusing storage shouldn't break the toggle itself.
      }
      applyColumns();
    });
  });

  document.addEventListener("click", (event) => {
    if (!columnMenu.contains(event.target)) columnMenu.removeAttribute("open");
  });

  applyColumns();
}

const ledgerForm = document.getElementById("ledger-form");
const projectBar = document.getElementById("project-assign-bar");

if (ledgerForm) {
  const selectAll = document.getElementById("ledger-select-all");
  const boxes = () => [...ledgerForm.querySelectorAll('input[name="ids"]')];
  let lastToggled = null;

  function updateProjectBar() {
    const checked = boxes().filter((box) => box.checked);

    if (selectAll) {
      selectAll.checked = checked.length > 0 && checked.length === boxes().length;
      selectAll.indeterminate = checked.length > 0 && checked.length < boxes().length;
    }

    if (!projectBar) return;
    projectBar.classList.toggle("hidden", checked.length === 0);
    const countEl = projectBar.querySelector("[data-selected-count]");
    if (countEl) countEl.textContent = String(checked.length);
  }

  ledgerForm.addEventListener("change", (event) => {
    if (event.target.name === "ids") updateProjectBar();
  });

  // Shift-click extends from the last box touched, so a run of transactions
  // can be handed to a project without clicking each one.
  ledgerForm.addEventListener("click", (event) => {
    const box = event.target.closest('input[name="ids"]');
    if (!box) return;

    if (event.shiftKey && lastToggled && lastToggled !== box) {
      const all = boxes();
      const [from, to] = [all.indexOf(lastToggled), all.indexOf(box)].sort((a, b) => a - b);
      all.slice(from, to + 1).forEach((other) => { other.checked = box.checked; });
      updateProjectBar();
    }

    lastToggled = box;
  });

  selectAll?.addEventListener("change", () => {
    boxes().forEach((box) => { box.checked = selectAll.checked; });
    lastToggled = null;
    updateProjectBar();
  });

  updateProjectBar();
}
