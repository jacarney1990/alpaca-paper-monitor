'use strict';

// Cache only public, non-user-specific display assets. Private missions,
// model responses, credentials, /api routes, and the workspace page NEVER
// enter the service worker cache.
const CACHE = 'nexus-shell-v4';
const PUBLIC_ASSETS = [
  '/static/nexus-icon.svg',
  '/static/nexus-icon-192.png',
  '/static/nexus-icon-512.png',
  '/static/offline.html',
  '/static/style.css',
  '/static/install.js',
  '/static/demo.js'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PUBLIC_ASSETS))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('nexus-shell-') && key !== CACHE)
      .map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    // The public demo and private workspace are always fetched fresh;
    // neither is ever stored in Cache Storage.
    event.respondWith(fetch(request).catch(() => caches.match('/static/offline.html')));
    return;
  }

  if (PUBLIC_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(url.pathname)
      .then(saved => saved || fetch(request)));
  }
  // All other resources, in particular /api/* and /download, use normal
  // browser network behavior with no service-worker caching.
});
