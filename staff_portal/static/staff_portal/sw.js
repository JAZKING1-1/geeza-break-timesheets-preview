/* Intentionally network-only. Never persist timesheets, sessions or account links. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET" || event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => new Response(
    '<!doctype html><html lang="en-GB"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect to Geeza Break</title><body><main><h1>You are offline</h1><p>Connect to the internet to open your Geeza Break timesheets.</p><p>This device does not store offline copies of your timesheets. A submission is complete only when the portal confirms it.</p><p><a href="/staff/">Try again</a></p></main></body></html>',
    {status:503, headers:{"Content-Type":"text/html; charset=utf-8", "Cache-Control":"no-store"}}
  )));
});
