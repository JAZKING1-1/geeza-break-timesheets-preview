(() => {
  const notice = document.getElementById("connection-notice");
  function connectionChanged() { if (notice) notice.hidden = navigator.onLine; }
  window.addEventListener("offline", connectionChanged);
  window.addEventListener("online", connectionChanged);
  connectionChanged();
  document.addEventListener("submit", event => {
    if (navigator.onLine || event.target.method.toLowerCase() !== "post") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (notice) {
      notice.hidden = false;
      notice.scrollIntoView({block:"center"});
      notice.setAttribute("tabindex", "-1");
      notice.focus();
    }
  }, true);
  const menu = document.querySelector(".mobile-menu");
  document.addEventListener("click", event => {
    if (menu?.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && menu?.open) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });
  let installation;
  const installButton = document.getElementById("install-app");
  window.addEventListener("beforeinstallprompt", event => {
    if (!installButton) return;
    event.preventDefault();
    installation = event;
    installButton.hidden = false;
  });
  installButton?.addEventListener("click", async () => {
    if (!installation) return;
    await installation.prompt();
    installation = null;
    installButton.hidden = true;
  });
  window.addEventListener("appinstalled", () => { if (installButton) installButton.hidden = true; });
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/staff/sw.js", {scope:"/staff/"}).catch(() => {
      // Normal browser use remains available if installation is unsupported.
    });
  }
})();
