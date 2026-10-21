const VERSION = "__VERSION__";
const CACHE = "intensity-static-" + VERSION;
const ASSETS = ["/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];
self.addEventListener("install", event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS))); });
self.addEventListener("activate", event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("intensity-static-") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("message", event => { if (event.data?.type === "SKIP_WAITING") self.skipWaiting(); });
self.addEventListener("fetch", event => {
  const req = event.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.mode === "navigate" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return;
  if (!(url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest")) return;
  event.respondWith(fetch(req).then(response => {
    if (response.ok && response.type === "basic") {
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put(req, copy)).catch(() => {});
    }
    return response;
  }).catch(() => caches.match(req)));
});
