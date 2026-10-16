(() => {
  const container = document.querySelector('[data-turnstile]');
  if (!container) return;
  const form = container.closest('form');
  const submit = form.querySelector('[data-captcha-submit]');
  const status = form.querySelector('#captcha-status');
  const retry = form.querySelector('[data-captcha-retry]');
  const reload = form.querySelector('[data-captcha-reload]');
  let widgetId;
  let verified = false;

  function update(ready, message, recover = false) {
    verified = ready;
    submit.disabled = !ready;
    status.textContent = message;
    retry.hidden = !recover || widgetId === undefined;
    reload.hidden = !recover;
  }

  const slowTimer = setTimeout(() => {
    if (!verified) update(false, 'The security check is taking longer than expected. Try again or reload this page.', true);
  }, 15000);

  window.portalTurnstileReady = () => {
    if (widgetId !== undefined) return;
    try {
      widgetId = window.turnstile.render(container, {
        sitekey: container.dataset.sitekey,
        action: container.dataset.action,
        theme: 'light',
        size: container.clientWidth < 300 ? 'compact' : 'flexible',
        'refresh-expired': 'auto',
        'refresh-timeout': 'auto',
        callback: () => {
          clearTimeout(slowTimer);
          update(true, 'Verification complete. You can continue.');
        },
        'expired-callback': () => update(false, 'Verification expired. Please wait for a fresh security check.', true),
        'timeout-callback': () => update(false, 'The security check timed out. Please try verification again.', true),
        'error-callback': () => {
          update(false, 'The security check could not finish. Please try verification again.', true);
          return true;
        },
      });
    } catch {
      update(false, 'The security check could not load. Reload this page to try again.', true);
    }
  };

  retry.addEventListener('click', () => {
    if (widgetId === undefined) return;
    update(false, 'Please wait for the security check before continuing.');
    window.turnstile.reset(widgetId);
  });
  document.getElementById('turnstile-api').addEventListener('error', () => {
    update(false, 'The security check could not load. Check your connection and reload this page.', true);
  });
  form.addEventListener('submit', event => {
    if (!verified || widgetId === undefined || window.turnstile.isExpired(widgetId) || !window.turnstile.getResponse(widgetId)) {
      event.preventDefault();
      update(false, 'Please finish the security check before continuing.', true);
      status.focus();
    }
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted && widgetId !== undefined) {
      update(false, 'Please wait for a fresh security check.');
      window.turnstile.reset(widgetId);
    }
  });
})();
