const INBOX_LOCKS_KEY = "inbox-locks";

function readLocks() {
  try {
    return JSON.parse(window.localStorage.getItem(INBOX_LOCKS_KEY)) || {};
  } catch {
    return {};
  }
}

function writeLocks(locks) {
  window.localStorage.setItem(INBOX_LOCKS_KEY, JSON.stringify(locks));
}

function rowFields(row) {
  return {
    checkbox: row.querySelector('input[name="ids"]'),
    payee: row.querySelector('input[name^="payee_"]'),
    account: row.querySelector('input[name^="account_"]'),
  };
}

function lockRow(row) {
  const { checkbox, payee, account } = rowFields(row);
  if (!checkbox) return;
  const locks = readLocks();
  locks[checkbox.value] = { payee: payee?.value || "", account: account?.value || "" };
  writeLocks(locks);
}

function unlockRow(row) {
  const { checkbox } = rowFields(row);
  if (!checkbox) return;
  const locks = readLocks();
  delete locks[checkbox.value];
  writeLocks(locks);
}

const inboxData = document.getElementById("inbox-data");
// Payee name -> the category it defaults to, and the names that already
// exist, so a row can say what saving it would create before you save it.
const payeeDefaults = new Map();
const knownPayees = new Set();
const knownAccounts = new Set();

// Mirrors payees.normalize(): the server matches a typed payee against
// payees.normalized_name, not against the text as typed.
function normalizePayee(value) {
  return (value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

if (inboxData) {
  for (const [payee, account] of Object.entries(JSON.parse(inboxData.dataset.payeeDefaults))) {
    payeeDefaults.set(normalizePayee(payee), account);
  }
  for (const name of JSON.parse(inboxData.dataset.accountNames)) {
    knownAccounts.add(name.trim().toLowerCase());
  }
}

document.querySelectorAll("#payee-options option").forEach((option) => {
  knownPayees.add(normalizePayee(option.value));
});

// Picking a payee pulls in its default category, but never over a category
// typed by hand - only an empty box, or one a previous autofill wrote and
// that the payee has since moved on from.
function autofillCategory(payeeInput) {
  const row = payeeInput.closest("tr");
  if (!row) return;
  const { account } = rowFields(row);
  if (!account || (account.value && !account.dataset.autofilled)) return;

  const preset = payeeDefaults.get(normalizePayee(payeeInput.value));

  if (preset) {
    account.value = preset;
    account.dataset.autofilled = "1";
  } else {
    account.value = "";
    delete account.dataset.autofilled;
  }
}

function lockedRows() {
  return [...document.querySelectorAll('#inbox-form input[name="ids"]')]
    .filter((checkbox) => checkbox.checked)
    .map((checkbox) => checkbox.closest("tr"))
    .filter(Boolean);
}

function txnCount(row) {
  const members = row.querySelector('input[name^="members_"]');
  return members ? members.value.split(",").filter(Boolean).length : 1;
}

function setStatus(key, value) {
  const el = document.querySelector(`[data-status="${key}"]`);
  if (!el) return;
  el.textContent = value;
  el.classList.toggle("text-text", value > 0);
  el.classList.toggle("text-muted", value === 0);
}

function updateStatus() {
  // Follows what _assign() would do with the locked rows: a category that
  // isn't the payee's default is stored as a per-transaction override, and
  // a payee with no default takes the first category assigned to it - so
  // later rows for that payee are compared against that one. A row with no
  // payee at all skips overrides entirely - it just teaches a rule.
  const defaults = new Map(payeeDefaults);
  const newPayees = new Set();
  const newAccounts = new Set();
  const newRules = new Set();
  let overrides = 0;
  let locked = 0;

  for (const row of lockedRows()) {
    locked += 1;
    const { payee, account } = rowFields(row);
    const payeeName = (payee?.value || "").trim();
    const category = (account?.value || "").trim();

    // A row with no category is rejected on save, so it creates nothing.
    if (!category) continue;

    if (!knownAccounts.has(category.toLowerCase())) newAccounts.add(category.toLowerCase());

    if (!payeeName) {
      // No payee to hang a default off: the description is learned as a
      // rule instead, flagged for review. No override is written.
      newRules.add(row.dataset.sortLabel);
      continue;
    }

    const key = normalizePayee(payeeName);
    if (!knownPayees.has(key)) newPayees.add(key);

    const fallback = defaults.get(key);
    if (fallback === undefined) {
      defaults.set(key, category);
    } else if (fallback.toLowerCase() !== category.toLowerCase()) {
      overrides += txnCount(row);
    }
  }

  setStatus("rules", newRules.size);
  setStatus("overrides", overrides);
  setStatus("payees", newPayees.size);
  setStatus("accounts", newAccounts.size);

  const scope = document.querySelector("[data-status-scope]");
  if (scope) scope.textContent = locked ? `${locked} locked row${locked === 1 ? "" : "s"}` : "Nothing locked";
}

const currentGroupIds = new Set(
  [...document.querySelectorAll('#inbox-form input[name="ids"]')].map((el) => el.value)
);
const prunedLocks = readLocks();
for (const id of Object.keys(prunedLocks)) {
  if (!currentGroupIds.has(id)) delete prunedLocks[id];
}
writeLocks(prunedLocks);

document.querySelectorAll('#inbox-form input[name="ids"]').forEach((checkbox) => {
  const row = checkbox.closest("tr");
  const lock = prunedLocks[checkbox.value];
  if (lock) {
    checkbox.checked = true;
    const { payee, account } = rowFields(row);
    if (payee && !payee.value) payee.value = lock.payee;
    if (account && !account.value) account.value = lock.account;
  }
  row?.classList.toggle("bg-positive/10", checkbox.checked);
});

updateStatus();

document.getElementById("inbox-select-all")?.addEventListener("change", (event) => {
  document.querySelectorAll('#inbox-form input[name="ids"]').forEach((el) => {
    const row = el.closest("tr");
    // Search hides rows rather than removing them; select-all shouldn't
    // silently pick up the ones that were filtered out.
    if (row?.classList.contains("hidden")) return;
    el.checked = event.target.checked;
    row?.classList.toggle("bg-positive/10", el.checked);
    if (row) event.target.checked ? lockRow(row) : unlockRow(row);
  });
  updateStatus();
});

document.querySelectorAll('#inbox-form input[name="ids"]').forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    const row = checkbox.closest("tr");
    row?.classList.toggle("bg-positive/10", checkbox.checked);
    if (!row) return;
    checkbox.checked ? lockRow(row) : unlockRow(row);
    updateStatus();
  });
});

document.querySelectorAll('#inbox-form input[name^="payee_"]').forEach((input) => {
  input.addEventListener("change", () => autofillCategory(input));
});

document.querySelectorAll('#inbox-form input[name^="account_"]').forEach((input) => {
  // Typed over: the box is the user's now, and stops following the payee.
  input.addEventListener("input", () => delete input.dataset.autofilled);
});

document.querySelectorAll('#inbox-form input[name^="payee_"], #inbox-form input[name^="account_"]').forEach((input) => {
  input.addEventListener("input", updateStatus);
  input.addEventListener("change", () => {
    const row = input.closest("tr");
    const { checkbox } = rowFields(row);
    if (checkbox?.checked) lockRow(row);
    updateStatus();
  });
});

function navigateWithToggles() {
  const params = new URLSearchParams();
  if (document.getElementById("smart-toggle")?.checked) params.set("smart", "1");
  if (document.getElementById("flat-toggle")?.checked) params.set("flat", "1");
  const query = params.toString();
  window.location.href = window.location.pathname + (query ? "?" + query : "");
}

document.getElementById("smart-toggle")?.addEventListener("change", navigateWithToggles);
document.getElementById("flat-toggle")?.addEventListener("change", navigateWithToggles);

document.getElementById("inbox-search")?.addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  let visible = 0;

  document.querySelectorAll('#inbox-form .table-wrap tbody > tr[data-sort-date]').forEach((row) => {
    const match = row.textContent.toLowerCase().includes(query);
    row.classList.toggle("hidden", !match);
    if (match) visible += 1;
    const next = row.nextElementSibling;
    if (!match && next && next.id.startsWith("group-details-")) {
      next.classList.add("hidden");
    }
  });

  document.getElementById("inbox-no-matches")?.classList.toggle("hidden", visible > 0);
});

const sortTbody = document.querySelector("#inbox-form .table-wrap tbody");

function collectRowPairs() {
  const pairs = [];
  sortTbody?.querySelectorAll(":scope > tr[data-sort-date]").forEach((row) => {
    const next = row.nextElementSibling;
    const detail = next && next.id.startsWith("group-details-") ? next : null;
    pairs.push({ row, detail });
  });
  return pairs;
}

const defaultOrder = collectRowPairs();
let activeSort = { column: null, dir: "asc" };

function applySort() {
  let pairs = defaultOrder;

  if (activeSort.column) {
    const key = "sort" + activeSort.column[0].toUpperCase() + activeSort.column.slice(1);
    pairs = [...defaultOrder].sort((a, b) => {
      const av = a.row.dataset[key];
      const bv = b.row.dataset[key];
      const cmp = activeSort.column === "amount"
        ? parseFloat(av) - parseFloat(bv)
        : av.localeCompare(bv);
      return activeSort.dir === "asc" ? cmp : -cmp;
    });
  }

  for (const { row, detail } of pairs) {
    sortTbody.appendChild(row);
    if (detail) sortTbody.appendChild(detail);
  }
}

document.querySelectorAll("[data-sort-header]").forEach((header) => {
  header.addEventListener("click", () => {
    const column = header.dataset.sortHeader;

    if (activeSort.column !== column) {
      activeSort = { column, dir: "asc" };
    } else if (activeSort.dir === "asc") {
      activeSort = { column, dir: "desc" };
    } else {
      activeSort = { column: null, dir: "asc" };
    }

    document.querySelectorAll("[data-sort-header]").forEach((otherHeader) => {
      const arrow = otherHeader.querySelector("[data-sort-arrow]");
      const active = otherHeader.dataset.sortHeader === activeSort.column;
      arrow?.classList.toggle("hidden", !active);
      arrow?.classList.toggle("rotate-180", active && activeSort.dir === "desc");
    });

    applySort();
  });
});
