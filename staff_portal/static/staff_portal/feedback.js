(() => {
  const sessionFeedback = document.querySelector("[data-session-feedback]");
  if (sessionFeedback) {
    const applies = sessionFeedback.querySelector("[name=applies]");
    const syncFeedback = () => {
      sessionFeedback.querySelector("[data-feedback-questions]").hidden = applies.value === "no";
      sessionFeedback.querySelector("[data-feedback-exemption]").hidden = applies.value !== "no";
      ["outdoor", "medication"].forEach(name => {
        const select = sessionFeedback.querySelector(`[name="${name}"]`);
        select.closest(".form-grid").querySelectorAll(".field").forEach(field => {
          field.hidden = !field.contains(select) && select.value === "no";
        });
      });
    };
    sessionFeedback.addEventListener("change", syncFeedback);
    syncFeedback();
  }
})();
