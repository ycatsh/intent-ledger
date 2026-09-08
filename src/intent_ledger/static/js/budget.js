const form = document.querySelector("[data-budget]");

if (form) {
  const rail = form.querySelector("[data-rail]");
  const railTitle = rail?.querySelector("[data-rail-title]");
  const inputs = [...form.querySelectorAll("[data-budget-input]")];

  function number(value) {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function setAmount(el, value) {
    if (!el) return;
    el.dataset.value = value.toFixed(2);
    el.textContent = value.toFixed(2);
    el.classList.toggle("amount-negative", value < 0);
    el.classList.toggle("amount-positive", value >= 0);
  }

  const assignedAtLoad = inputs.reduce((sum, input) => sum + number(input.dataset.assigned), 0);
  const toBudgetEl = document.querySelector('[data-total="to_budget"]');
  const toBudgetBaseline = number(toBudgetEl?.dataset.value) + assignedAtLoad;

  function recalcBudgetTotals() {
    let assigned = 0;
    let actual = 0;
    let rollover = 0;

    inputs.forEach((input) => {
      const value = number(input.value);
      const carryIn = number(input.dataset.carryIn);
      const spent = number(input.dataset.actual);
      const left = carryIn + value + spent;

      setAmount(form.querySelector(`[data-row-left="${input.dataset.row}"]`), left);

      assigned += value;
      actual += spent;
      rollover += left;
    });

    document.querySelectorAll('[data-total="assigned"]').forEach((el) => setAmount(el, assigned));
    document.querySelectorAll('[data-total="actual"]').forEach((el) => setAmount(el, -actual));
    document.querySelectorAll('[data-total="left"]').forEach((el) => setAmount(el, assigned + actual));
    document.querySelectorAll('[data-total="rollover"]').forEach((el) => setAmount(el, rollover));

    document.querySelectorAll('[data-total="to_budget"]').forEach((el) => {
      setAmount(el, toBudgetBaseline - assigned);
    });
  }

  function fillAll(preset) {
    inputs.forEach((input) => {
      const value = preset === "zero" ? "0.00" : input.dataset[preset];
      if (value === undefined || value === "") return;
      input.value = value;
    });
    recalcBudgetTotals();
  }

  function openRail(id) {
    if (!rail) return;

    rail.querySelectorAll("[data-rail-block]").forEach((block) => {
      block.classList.toggle("hidden", block.dataset.railBlock !== id);
    });

    form.querySelectorAll("[data-row]").forEach((row) => {
      row.classList.toggle("budget-row-active", row.dataset.row === id);
    });

    const active = rail.querySelector(`[data-rail-block="${id}"]`);
    if (railTitle && active) {
      railTitle.textContent = active.dataset.name;
    }

    rail.classList.add("budget-rail-open");

    if (active) {
      renderFinanceCharts(active);
    }
  }

  function closeRail() {
    rail?.classList.remove("budget-rail-open");
    form.querySelectorAll("[data-row]").forEach((row) => row.classList.remove("budget-row-active"));
  }

  form.addEventListener("input", (event) => {
    if (event.target.matches("[data-budget-input]")) {
      recalcBudgetTotals();
    }
  });

  form.addEventListener("click", (event) => {
    if (event.target.closest("[data-rail-close]")) {
      closeRail();
      return;
    }

    if (event.target.closest("[data-rail]")) return;

    const row = event.target.closest("[data-row]");
    if (!row || event.target.closest("input, select, button, a")) return;

    if (row.classList.contains("budget-row-active")) {
      closeRail();
    } else {
      openRail(row.dataset.row);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeRail();
  });

  document.querySelector("[data-bulk-fill]")?.addEventListener("change", (event) => {
    if (event.target.value) {
      fillAll(event.target.value);
      event.target.value = "";
      Combobox.sync();
    }
  });

  document.querySelectorAll("[data-bulk-fill-value]").forEach((button) => {
    button.addEventListener("click", () => {
      fillAll(button.dataset.bulkFillValue);
      button.closest("details")?.removeAttribute("open");
    });
  });

  recalcBudgetTotals();
}
