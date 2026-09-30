// Native details works without JavaScript; these add Escape and outside-click support.
"use strict";
const servicesMenu = document.querySelector(".services-menu");
if (servicesMenu) {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && servicesMenu.open) {
      const returnFocus = servicesMenu.contains(document.activeElement);
      servicesMenu.open = false;
      if (returnFocus) servicesMenu.querySelector("summary").focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (servicesMenu.open && !servicesMenu.contains(event.target)) servicesMenu.open = false;
  });
  servicesMenu.addEventListener("focusout", () => {
    requestAnimationFrame(() => {
      if (!servicesMenu.contains(document.activeElement)) servicesMenu.open = false;
    });
  });
}
