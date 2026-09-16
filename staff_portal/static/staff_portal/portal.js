(() => {
  document.querySelectorAll("[data-demo-email]").forEach(button => button.addEventListener("click", () => {
    document.getElementById("id_email").value = button.dataset.demoEmail;
    document.getElementById("id_password").value = button.dataset.demoPassword;
    document.querySelector("button[type=submit]")?.focus();
  }));
  const summary = document.querySelector(".error-summary");
  if (summary) summary.focus();
  document.querySelectorAll("[data-print]").forEach(button => button.addEventListener("click", () => window.print()));
  document.querySelectorAll("form[data-confirm]").forEach(form => form.addEventListener("submit", event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));
  let dirty = false;
  document.querySelectorAll("form[data-unsaved]").forEach(form => {
    form.addEventListener("input", () => { dirty = true; });
    form.addEventListener("submit", event => { if (!event.defaultPrevented) dirty = false; });
  });
  const editor = document.querySelector("[data-month-editor]");
  if (editor) {
    const total = editor.querySelector('[name="rows-TOTAL_FORMS"]');
    const max = Number(editor.querySelector('[name="rows-MAX_NUM_FORMS"]').value);
    const announcement = document.getElementById("row-announcement");
    document.getElementById("add-row").addEventListener("click", event => {
      event.preventDefault();
      const index = Number(total.value);
      if (index >= max) { announcement.textContent = "This timesheet supports up to " + max + " rows."; return; }
      const template = document.getElementById("empty-entry-row");
      const copy = document.createElement("template");
      copy.innerHTML = template.innerHTML.replaceAll("__prefix__", index).replaceAll("__number__", index + 1);
      const row = copy.content.querySelector("[data-entry-row]");
      document.getElementById("entry-rows").appendChild(row);
      total.value = index + 1;
      row.querySelector('select[name$="-date"]').focus();
      announcement.textContent = "Row " + (index + 1) + " added. Choose its date to begin.";
    });
    editor.addEventListener("change", event => {
      if (!event.target.matches('input[name$="-DELETE"]')) return;
      const row = event.target.closest("[data-entry-row]");
      row.classList.toggle("pending-removal", event.target.checked);
      row.querySelector(".removal-note").hidden = !event.target.checked;
      announcement.textContent = event.target.checked ? "Row marked for removal when you save. Untick it to undo." : "Row kept.";
    });
  }
  window.addEventListener("beforeunload", event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  const warning = document.getElementById("session-warning");
  let warningTimer;
  function resetWarning() {
    if (!warning) return;
    warning.hidden = true;
    clearTimeout(warningTimer);
    warningTimer = setTimeout(() => { warning.hidden = false; warning.setAttribute("role", "alert"); }, 28 * 60 * 1000);
  }
  document.getElementById("keep-session")?.addEventListener("click", async () => {
    const token = document.querySelector("input[name=csrfmiddlewaretoken]")?.value;
    try {
      const response = await fetch("/staff/keep-session/", {method: "POST", headers: {"X-CSRFToken": token}, credentials: "same-origin"});
      if (!response.ok || response.redirected) throw new Error();
      resetWarning();
    } catch {
      warning.textContent = "Your session could not be extended. Save a private copy of any unsaved notes, then sign in again.";
    }
  });
  resetWarning();
})();
