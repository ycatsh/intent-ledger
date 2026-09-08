/* Custom dropdown used for every picker in the app: text inputs backed by a
 * <datalist> and plain <select> elements both open the same filterable menu.
 * A <select> keeps its element (name, value, change events, validation), and
 * only its rendering is replaced, so page scripts can keep talking to it. */

const COMBOBOX_GAP = 4;
const COMBOBOX_MAX_HEIGHT = 192;
const COMBOBOX_MIN_HEIGHT = 80;
const COMBOBOX_VIEWPORT_MARGIN = 8;
const COMBOBOX_MAX_OPTIONS = 100;

const ITEM_CLASS =
  "block w-full truncate px-3 py-1.5 text-left text-sm text-text hover:bg-surface-hover";

// One menu is enough: only a single combobox can be open at a time, and pages
// like Mappings render hundreds of selects.
const menu = document.createElement("div");
menu.className =
  "combobox-menu hidden fixed z-50 overflow-y-auto rounded-md border border-border " +
  "bg-surface-raised shadow-xl";
menu.setAttribute("role", "listbox");
document.body.appendChild(menu);

// Page scripts toggle `disabled` on whole rows (Mappings deletes, for one);
// mirror that onto the input that actually stands in for the select.
const disabledObserver = new MutationObserver((records) => {
  for (const record of records) {
    const display = record.target.nextElementSibling;
    if (display?.dataset.comboboxDisplay !== undefined) display.disabled = record.target.disabled;
  }
});

let open = null;

function position(anchor) {
  const rect = anchor.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom - COMBOBOX_GAP;
  const spaceAbove = rect.top - COMBOBOX_GAP;
  const openAbove = spaceBelow < COMBOBOX_MIN_HEIGHT && spaceAbove > spaceBelow;
  const available = openAbove ? spaceAbove : spaceBelow;

  menu.style.maxHeight = `${Math.max(Math.min(available, COMBOBOX_MAX_HEIGHT), COMBOBOX_MIN_HEIGHT)}px`;
  menu.style.width = `${rect.width}px`;

  const left = Math.min(rect.left, window.innerWidth - rect.width - COMBOBOX_VIEWPORT_MARGIN);
  menu.style.left = `${Math.max(COMBOBOX_VIEWPORT_MARGIN, left)}px`;

  if (openAbove) {
    menu.style.top = "";
    menu.style.bottom = `${window.innerHeight - rect.top + COMBOBOX_GAP}px`;
  } else {
    menu.style.bottom = "";
    menu.style.top = `${rect.bottom + COMBOBOX_GAP}px`;
  }
}

function closeMenu() {
  if (!open) return;
  open.anchor.setAttribute("aria-expanded", "false");
  open = null;
  menu.classList.add("hidden");
  menu.textContent = "";
}

function render() {
  menu.textContent = "";

  if (!open || open.items.length === 0) {
    menu.classList.add("hidden");
    return;
  }

  open.items.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "option");
    button.className = index === open.index ? `${ITEM_CLASS} bg-surface-hover` : ITEM_CLASS;
    button.textContent = item.label;
    // mousedown, not click: the anchor's blur would close the menu first.
    button.addEventListener("mousedown", (event) => {
      event.preventDefault();
      choose(index);
    });
    menu.appendChild(button);
  });

  menu.classList.remove("hidden");
  position(open.anchor);
  menu.children[Math.max(open.index, 0)]?.scrollIntoView({ block: "nearest" });
}

function openMenu({ anchor, items, index, onPick }) {
  open = { anchor, items: items.slice(0, COMBOBOX_MAX_OPTIONS), index, onPick };
  anchor.setAttribute("aria-expanded", "true");

  // A modal <dialog> paints in the top layer, above anything left in the body,
  // so the menu has to move into the dialog to stay visible.
  (anchor.closest("dialog[open]") || document.body).appendChild(menu);

  render();
}

function choose(index) {
  if (!open) return;
  const { items, onPick } = open;
  const item = items[index];
  closeMenu();
  if (item) onPick(item);
}

function move(step) {
  if (!open || open.items.length === 0) return;
  const count = open.items.length;
  open.index = open.index < 0
    ? (step > 0 ? 0 : count - 1)
    : (open.index + step + count) % count;
  render();
}

function handleKeydown(event, show) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    open ? move(1) : show();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    open ? move(-1) : show();
  } else if (event.key === "Enter" && open && open.index >= 0) {
    event.preventDefault();
    choose(open.index);
  } else if (event.key === "Escape" || event.key === "Tab") {
    closeMenu();
  }
}

function matching(options, query) {
  const needle = query.trim().toLowerCase();
  return needle ? options.filter((o) => o.label.toLowerCase().includes(needle)) : options;
}

// Text input + <datalist>: free text stays allowed, the menu only suggests.
function initInput(input) {
  const datalist = document.getElementById(input.getAttribute("list"));
  if (!datalist) return;

  input.removeAttribute("list");
  input.setAttribute("autocomplete", "off");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "false");

  function show() {
    const options = [...datalist.options].map((option) => ({
      value: option.value,
      label: option.value,
    }));
    openMenu({
      anchor: input,
      items: matching(options, input.value),
      index: -1,
      onPick: (item) => {
        input.value = item.value;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      },
    });
  }

  input.addEventListener("input", show);
  input.addEventListener("focus", show);
  input.addEventListener("click", () => { if (!open) show(); });
  input.addEventListener("blur", closeMenu);
  input.addEventListener("keydown", (event) => handleKeydown(event, show));
}

// <select>: the element stays in the DOM (visually hidden but still focusable,
// so constraint validation can point at it) and a text input renders in its
// place, doubling as the filter box.
function initSelect(select) {
  if (select.multiple || select.options.length === 0) return;

  const display = document.createElement("input");
  display.type = "text";
  display.className = `${select.className} combobox-display`;
  display.autocomplete = "off";
  display.disabled = select.disabled;
  display.dataset.comboboxDisplay = "";
  display.setAttribute("role", "combobox");
  display.setAttribute("aria-expanded", "false");

  if (select.id) {
    display.id = `${select.id}-combobox`;
    document.querySelectorAll(`label[for="${select.id}"]`).forEach((label) => {
      label.setAttribute("for", display.id);
    });
  }
  if (select.getAttribute("aria-label")) {
    display.setAttribute("aria-label", select.getAttribute("aria-label"));
  }

  select.classList.add("combobox-native");
  select.setAttribute("tabindex", "-1");
  select.after(display);

  function sync() {
    display.value = select.selectedOptions[0]?.text.trim() || "";
  }

  function show() {
    const options = [...select.options]
      .filter((option) => !option.disabled)
      .map((option) => ({ value: option.value, label: option.text.trim() }));
    const items = matching(options, display.dataset.typed ? display.value : "");

    openMenu({
      anchor: display,
      items,
      index: items.findIndex((item) => item.value === select.value),
      onPick: (item) => {
        select.value = item.value;
        sync();
        select.dispatchEvent(new Event("change", { bubbles: true }));
      },
    });
  }

  display.addEventListener("focus", () => {
    delete display.dataset.typed;
    display.select();
    show();
  });
  display.addEventListener("click", () => { if (!open) show(); });
  display.addEventListener("input", () => {
    display.dataset.typed = "1";
    show();
  });
  display.addEventListener("blur", () => {
    closeMenu();
    delete display.dataset.typed;
    sync();
  });
  display.addEventListener("keydown", (event) => handleKeydown(event, show));
  select.addEventListener("change", sync);

  disabledObserver.observe(select, { attributes: true, attributeFilter: ["disabled"] });
  select.comboboxSync = sync;
  sync();
}

function enhance(root = document) {
  root.querySelectorAll("input[list]").forEach(initInput);
  root.querySelectorAll("select:not(.combobox-native)").forEach(initSelect);
}

// Programmatic value changes (form resets, "fill all" pickers) don't fire
// `change`, so callers ask for a resync explicitly.
function sync(root = document) {
  root.querySelectorAll("select.combobox-native").forEach((select) => select.comboboxSync?.());
}

window.Combobox = { enhance, sync, close: closeMenu };

window.addEventListener("scroll", () => { if (open) position(open.anchor); }, true);
window.addEventListener("resize", () => { if (open) position(open.anchor); });

enhance();
