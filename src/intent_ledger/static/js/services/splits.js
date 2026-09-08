/* The split editor: any number of lines, with a running count of how much of
 * the transaction is still unassigned. The server is still the authority on
 * whether a split balances - this only saves the user from finding out after
 * a round trip. */

const editor = document.querySelector("[data-split-editor]");

if (editor) {
  const MIN_LINES = 2;

  const rows = editor.querySelector("[data-split-rows]");
  const template = editor.querySelector("[data-split-template]");
  const remainingEl = editor.querySelector("[data-split-remaining]");
  const total = Number(editor.dataset.splitTotal) || 0;

  const money = (value) =>
    value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function lines() {
    return [...rows.querySelectorAll("[data-split-row]")];
  }

  function remaining() {
    const assigned = lines().reduce(
      (sum, row) => sum + (parseFloat(row.querySelector("[data-split-amount]").value) || 0),
      0,
    );
    // Cents, so the comparison against zero survives float addition.
    return Math.round((total - assigned) * 100) / 100;
  }

  function refresh() {
    const left = remaining();
    const balanced = left === 0;

    remainingEl.textContent = money(left);
    remainingEl.classList.toggle("amount-positive", balanced);
    remainingEl.classList.toggle("amount-negative", !balanced);

    const current = lines();
    current.forEach((row) => {
      row.querySelector("[data-split-remove]").disabled = current.length <= MIN_LINES;
    });
  }

  editor.querySelector("[data-split-add]")?.addEventListener("click", () => {
    const row = template.content.firstElementChild.cloneNode(true);
    rows.appendChild(row);
    Combobox.enhance(row);
    refresh();
    row.querySelector("[data-split-amount]").focus();
  });

  // Drop whatever is unassigned onto the last line, which is where a split
  // built top-down leaves the odd cent.
  editor.querySelector("[data-split-balance]")?.addEventListener("click", () => {
    const last = lines().at(-1);
    if (!last) return;

    const amount = last.querySelector("[data-split-amount]");
    amount.value = ((parseFloat(amount.value) || 0) + remaining()).toFixed(2);
    refresh();
  });

  rows.addEventListener("input", refresh);
  rows.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-split-remove]");
    if (!remove || lines().length <= MIN_LINES) return;
    remove.closest("[data-split-row]").remove();
    refresh();
  });

  refresh();
}
