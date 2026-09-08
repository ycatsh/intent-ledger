const parserSelect = document.getElementById("parser_slug");

document.getElementById("account-search")?.addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  const rows = document.querySelectorAll("#dropzone-list [data-dropzone]");
  let visible = 0;

  rows.forEach((row) => {
    const match = row.dataset.accountName.includes(query);
    row.classList.toggle("hidden", !match);
    if (match) visible += 1;
  });

  document.getElementById("dropzone-no-matches")?.classList.toggle("hidden", visible > 0 || rows.length === 0);
});

document.querySelectorAll("[data-dropzone]").forEach((zone) => {
  const input = zone.querySelector("[data-dropzone-input]");
  const parserField = zone.querySelector("[data-parser-slug-field]");

  zone.addEventListener("click", (event) => {
    if (event.target === input) return;
    input.click();
  });

  input.addEventListener("change", () => {
    if (input.files.length) zone.requestSubmit();
  });

  zone.addEventListener("submit", () => {
    if (parserField && parserSelect) parserField.value = parserSelect.value;
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
