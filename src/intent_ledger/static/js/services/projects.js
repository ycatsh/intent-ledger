document.getElementById("project-picker")?.addEventListener("change", (event) => {
  for (const option of document.getElementById("project-options").options) {
    if (option.value === event.target.value) {
      window.location.href = `${event.target.dataset.url}?project_id=${option.dataset.id}`;
      return;
    }
  }
});
