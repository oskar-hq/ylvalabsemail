// Service Worker der Web-App „Ylva Leads“: zeigt Push-Benachrichtigungen an und öffnet beim Antippen die passende Seite.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { title: 'Ylva Leads', body: event.data?.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || 'Ylva Leads', {
    body: data.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    data: { url: data.url || '/leads' },
    tag: data.url || 'leads',
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || '/leads', self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const win of wins) {
      if ('focus' in win) { await win.focus(); if ('navigate' in win) return win.navigate(url); return; }
    }
    return self.clients.openWindow(url);
  })());
});
