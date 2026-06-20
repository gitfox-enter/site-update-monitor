const CACHE_NAME = 'rssforge-v1';
const BASE = new URL('.', self.location.href).pathname.replace(/\/$/, '');
const ASSETS = [
  BASE + '/index.html',
  BASE + '/public/favicon.svg',
  BASE + '/offline.html',
  BASE + '/redirect.html',
  BASE + '/status.html',
  BASE + '/alipay-redpacket.html'
];

// === Install & Activate ===
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)).then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// === Fetch handler (network-first for data, cache-first for assets) ===
self.addEventListener('fetch', e => {
  if (e.request.url.includes('items.json') || e.request.url.includes('items_latest.json') ||
      e.request.headers.get('accept')?.includes('text/html') ||
      e.request.url.endsWith('.html') || e.request.url.endsWith('/')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
          return res;
        })
        .catch(async () => {
          const cached = await caches.match(e.request);
          if (cached) return cached;
          if (e.request.mode === 'navigate') {
            const offlinePage = await caches.match(BASE + '/offline.html');
            if (offlinePage) return offlinePage;
          }
          return new Response('离线 - RSSForge', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
        })
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
      return res;
    }))
  );
});
