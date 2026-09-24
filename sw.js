// Network-first so the UI is always fresh online (matches no-cache on index);
// the cached shell is only the offline fallback. /api/* is never cached (live data).
//
// Bump CACHE when the cached surface changes: activate() deletes every other
// cache, which is also the only pruning there is. Without it the old cache stayed
// forever next to the new one, and nothing here caps what goes in -- so keep the
// allowlist below tight (the shell only).
const CACHE = "yashome-v1";
const MAX_ENTRIES = 40;
const SKIP = [/^\/api\//];
const SHELL = ["/", "/i18n.js", "/manifest.json", "/icon-192.png"];

self.addEventListener("install", e => e.waitUntil((async () => {
  const c = await caches.open(CACHE);
  await c.addAll(SHELL);          // first offline visit has a shell to fall back on
  await self.skipWaiting();
})()));

self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
  await self.clients.claim();
})()));

async function trim(cache) {
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
    if (SHELL.includes(new URL(k.url).pathname)) continue;   // FIFO must never evict the shell
    await cache.delete(k);
  }
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (SKIP.some(re => re.test(url.pathname))) return;
  // cache under the bare path: a first visit with ?token=... must not leave the token
  // in the cache (it would outlive "Log out"), and one entry per page is enough
  const key = url.origin + url.pathname;
  e.respondWith(
    fetch(req, {cache: "no-store"}).then(res => {
      if (res && res.ok) {
        const copy = res.clone();
        e.waitUntil(caches.open(CACHE).then(async c => { await c.put(key, copy); await trim(c); }));
      }
      return res;
    }).catch(() => caches.match(key).then(r => r || caches.match("/")))
  );
});
