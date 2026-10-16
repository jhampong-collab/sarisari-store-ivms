/* Service worker: keeps the app opening instantly and delivers updates.
   The version below is filled in by the server (APP_VERSION in app.py). When you bump
   that version, every phone downloads this file again, sees it changed, and shows an
   "Update available" banner. Store data is NEVER cached here; it always comes live
   from the server, so updates can never touch or lose it. */
const VERSION = "__VERSION__";
const CACHE = "sari-shell-" + VERSION;
const SHELL = [
  "/",
  "/static/app.js?v=" + VERSION,
  "/static/style.css?v=" + VERSION,
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  // Not skipWaiting() here: the new version waits until the user taps "Update".
  event.waitUntil(caches.open(CACHE).then((cache) =>
    cache.addAll(SHELL.map((url) => new Request(url, { cache: "reload" })))));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((k) => k.startsWith("sari-shell-") && k !== CACHE)
        .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;  // live data only, never cached
  // App files: network first (so changes show up right away), cache when offline.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/"))));
});
