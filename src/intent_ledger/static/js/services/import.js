const rows = [...document.querySelectorAll("#dropzone-list [data-dropzone]")];
const noMatches = document.getElementById("dropzone-no-matches");
let searchQuery = "";

const ACCOUNT_PREF_KEY = "import-hidden-accounts";
const accountMenu = document.querySelector("[data-account-menu]");
let hiddenAccounts;
try {
  hiddenAccounts = new Set(JSON.parse(window.localStorage.getItem(ACCOUNT_PREF_KEY)) || []);
} catch {
  hiddenAccounts = new Set();
}

function updateVisibility() {
  let visible = 0;

  rows.forEach((row) => {
    const show = row.dataset.accountName.includes(searchQuery) && !hiddenAccounts.has(row.dataset.accountId);
    row.classList.toggle("hidden", !show);
    if (show) visible += 1;
  });

  noMatches?.classList.toggle("hidden", visible > 0 || rows.length === 0);
}

document.getElementById("account-search")?.addEventListener("input", (event) => {
  searchQuery = event.target.value.trim().toLowerCase();
  updateVisibility();
});

if (accountMenu) {
  const toggles = [...accountMenu.querySelectorAll("[data-account-toggle]")];

  toggles.forEach((toggle) => {
    toggle.checked = !hiddenAccounts.has(toggle.dataset.accountToggle);

    toggle.addEventListener("change", () => {
      const accountId = toggle.dataset.accountToggle;
      toggle.checked ? hiddenAccounts.delete(accountId) : hiddenAccounts.add(accountId);
      try {
        window.localStorage.setItem(ACCOUNT_PREF_KEY, JSON.stringify([...hiddenAccounts]));
      } catch {
      }
      updateVisibility();
    });
  });

  document.addEventListener("click", (event) => {
    if (!accountMenu.contains(event.target)) accountMenu.removeAttribute("open");
  });
}

updateVisibility();

document.querySelectorAll("[data-dropzone]").forEach((zone) => {
  const input = zone.querySelector("[data-dropzone-input]");

  input.addEventListener("change", () => {
    if (input.files.length) zone.requestSubmit();
  });

  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dropzone-active");
  });

  zone.addEventListener("dragleave", () => zone.classList.remove("dropzone-active"));

  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dropzone-active");
    if (!event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    zone.requestSubmit();
  });
});
