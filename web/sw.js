'use strict';

/* Service worker for privacy-safe Web Push notifications.
   Payloads contain only structural metadata and group-safe text.
   No private health, dietary, or GPS data is transported. */

self.addEventListener('push', event => {
  if (!event.data) return;
  let payload;
  try {
    payload = event.data.json();
  } catch (err) {
    payload = {
      title: 'Makan Together',
      body: event.data.text() || 'Updates available for your table.',
      icon: '/assets/favicon.svg',
    };
  }

  const title = payload.title || 'Makan Together';
  const options = {
    body: payload.body || 'Updates available for your table.',
    icon: payload.icon || '/assets/favicon.svg',
    badge: payload.badge || '/assets/favicon.svg',
    data: payload.data || {},
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const data = event.notification.data || {};
  let targetUrl = '/';
  if (data.meal_id) {
    targetUrl = `/#meal/${encodeURIComponent(data.meal_id)}`;
  } else if (data.room_id) {
    targetUrl = `/#room/${encodeURIComponent(data.room_id)}`;
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if ('focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
