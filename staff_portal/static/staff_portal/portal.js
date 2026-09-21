(() => {
  document.querySelectorAll("[data-calendar-for]").forEach(button => {
    const input = document.getElementById(button.dataset.calendarFor);
    if (!input || typeof input.showPicker !== "function") return;
    button.hidden = false;
    button.addEventListener("click", () => {
      try { input.showPicker(); } catch { input.focus(); }
    });
  });
  const summary = document.querySelector(".error-summary");
  if (summary) summary.focus();
  else document.querySelector(".messages .notice")?.focus();
  document.querySelectorAll("[data-print]").forEach(button => button.addEventListener("click", () => window.print()));
  document.querySelectorAll("form[data-confirm]").forEach(form => form.addEventListener("submit", event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));
  const saveState = document.getElementById("save-state");
  let dirty = saveState?.dataset.dirty === "true";
  function markUnsaved() {
    dirty = true;
    if (!saveState || saveState.classList.contains("has-changes")) return;
    saveState.classList.add("has-changes");
    saveState.querySelector("[data-save-label]").textContent = "You have unsaved changes.";
    saveState.querySelector("[data-save-detail]").textContent = "Choose Save draft to keep your work, or Submit timesheet when your month is complete.";
  }
  document.querySelectorAll("form[data-unsaved]").forEach(form => {
    form.addEventListener("input", markUnsaved);
    form.addEventListener("change", markUnsaved);
    form.addEventListener("submit", event => { if (!event.defaultPrevented) dirty = false; });
  });
  function showSelection(select) {
    select.classList.toggle("has-selection", select.value !== "");
  }
  document.querySelectorAll("select").forEach(showSelection);
  document.addEventListener("change", event => {
    if (event.target.matches("select")) showSelection(event.target);
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
      row.querySelectorAll("select").forEach(showSelection);
      row.querySelector('input[name$="-date"]').focus();
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
  // Keep the clicked button enabled so its name/value is included in the POST.
  // aria-disabled plus this submit guard prevents duplicate activation while waiting.
  const pendingForms = new Map();
  const feedback = document.getElementById("action-feedback");
  document.addEventListener("submit", event => {
    if (event.defaultPrevented) return;
    const form = event.target;
    // An unsaved-change prompt can cancel navigation to another view.
    if (dirty && !form.matches("[data-unsaved]")) return;
    if (pendingForms.has(form)) { event.preventDefault(); return; }
    const button = event.submitter;
    const message = button?.dataset.pendingLabel || (form.method === "get" ? "Loading results…" : "Please wait…");
    const contents = button ? Array.from(button.childNodes).map(node => node.cloneNode(true)) : [];
    pendingForms.set(form, {button, contents});
    form.setAttribute("aria-busy", "true");
    if (button) {
      button.setAttribute("aria-disabled", "true");
      button.classList.add("is-pending");
      button.textContent = message;
    }
    if (feedback) feedback.textContent = message;
  });
  // The browser may restore this page from its back/forward cache.
  window.addEventListener("pageshow", () => {
    pendingForms.forEach(({button, contents}, form) => {
      form.removeAttribute("aria-busy");
      if (button) {
        button.removeAttribute("aria-disabled");
        button.classList.remove("is-pending");
        button.replaceChildren(...contents);
      }
    });
    pendingForms.clear();
    if (feedback) feedback.textContent = "";
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
