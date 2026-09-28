// Service worker: shows the pushes sphere_web sends (notify.py) and opens the session's
// chat when one is tapped. Nothing is cached; the page is always fetched live.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {body: e.data.text()}; }
  // iOS revokes the subscription if a push doesn't show a notification, so always show one.
  e.waitUntil(self.registration.showNotification(d.title || "Floating Sphere", {
    body: d.body || "", tag: d.tag, renotify: !!d.tag, icon: "icon-192.png", badge: "icon-192.png",
    data: {id: d.id || null},
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(openSession(e.notification.data && e.notification.data.id));
});

// Bring the app up on that session's chat: the open window if there is one (the page
// handles {open: id}), else a new one on #<id>, which the page opens straight into.
async function openSession(id) {
  const wins = await self.clients.matchAll({type: "window", includeUncontrolled: true});
  if (wins.length) {
    const w = await wins[0].focus().catch(() => wins[0]);
    if (id) w.postMessage({open: id});
    return;
  }
  await self.clients.openWindow(new URL(id ? "./#" + encodeURIComponent(id) : "./", self.registration.scope).href);
}
