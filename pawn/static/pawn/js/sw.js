// Only Pawns service worker: keeps the app shell available offline.
// Money screens are never cached, only the look of the app and the offline page.
const CACHE = "onlypawns-v3";
const SHELL = ["/offline/", "/static/pawn/css/app.css", "/static/pawn/css/glass.css", "/static/pawn/js/theme-init.js", "/static/pawn/js/app.js", "/static/pawn/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin && url.pathname.startsWith("/static/")) {
    // network first so updates show up right away, cache only when offline
    e.respondWith(fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }).catch(() => caches.match(req)));
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/offline/")));
  }
});
