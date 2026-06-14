const CACHE_NAME = 'xianbao-v11';
const BASE = new URL('.', self.location.href).pathname.replace(/\/$/, '');
const ASSETS = [
  BASE + '/index.html',
  BASE + '/public/favicon.svg',
  BASE + '/offline.html',
  BASE + '/redirect.html',
  BASE + '/status.html',
  BASE + '/alipay-redpacket.html'
];
const POLL_INTERVAL = 15 * 60 * 1000; // 15 minutes (爬虫最快2小时更新，15分钟检查一次足够)
const NOTIFICATION_TAG = 'xianbao-update';
const LAST_COUNT_KEY = 'xb_last_item_count';
let lastItemCount = 0;

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
    ).then(function() {
      // Restore lastItemCount from IndexedDB or just use localStorage via clients
      // For simplicity, we'll fetch the current count first
      return fetch(BASE + '/items_latest.json').then(r => r.json()).then(data => {
        lastItemCount = (data.items || []).length;
      }).catch(() => {});
    })
  );
  self.clients.claim();
  // Start polling for updates after activation
  pollForUpdates();
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
          return new Response('离线 - 线报聚合', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
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

// === Push notification handler ===
self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  const title = data.title || '线报聚合';
  const body = data.body || '发现新的羊毛线报，点击查看！';
  e.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: BASE + '/public/icon-192.png',
      badge: BASE + '/public/favicon.svg',
      tag: NOTIFICATION_TAG,
      data: { url: data.url || BASE + '/' },
      vibrate: [100, 50, 100],
      requireInteraction: false,
      renotify: true
    })
  );
});

// === Notification click handler ===
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || BASE + '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url.includes(BASE) && 'focus' in client) return client.focus();
      }
      return clients.openWindow(url);
    })
  );
});

// === Polling: check items_latest.json for new items (managed by page, not SW) ===

// === Message handler from page ===
let pollingEnabled = true;

self.addEventListener('message', e => {
  if (e.data && e.data.type === 'START_POLLING') {
    pollingEnabled = true;
  } else if (e.data && e.data.type === 'STOP_POLLING') {
    pollingEnabled = false;
  }
});

// Modify pollForUpdates to respect pollingEnabled

async function pollForUpdates() {
  if (!pollingEnabled) { setTimeout(pollForUpdates, POLL_INTERVAL); return; }
  try {
    const res = await fetch(BASE + '/items_latest.json?t=' + Date.now());
    if (!res.ok) { setTimeout(pollForUpdates, POLL_INTERVAL); return; }
    const data = await res.json();
    const currentCount = (data.items || []).length;
    
    // First poll: just record the count, don't notify
    if (lastItemCount === 0) {
      lastItemCount = currentCount;
      setTimeout(pollForUpdates, POLL_INTERVAL);
      return;
    }
    
    // New items detected
    if (currentCount > lastItemCount) {
      const diff = currentCount - lastItemCount;
      const latest = data.items[0];
      const preview = latest ? latest.text?.substring(0, 60) : '';
      
      self.registration.showNotification('线报聚合 🐑', {
        body: diff === 1 && preview
          ? preview
          : `发现 ${diff} 条新线报，点击查看`,
        icon: BASE + '/public/icon-192.png',
        badge: BASE + '/public/favicon.svg',
        tag: NOTIFICATION_TAG,
        data: { url: BASE + '/' },
        vibrate: [100, 50, 100],
        renotify: true
      });
      
      lastItemCount = currentCount;
    }
  } catch (e) {
    // Poll failed, will retry next interval
  }
  
  // Schedule next poll
  setTimeout(pollForUpdates, POLL_INTERVAL);
}
