'use strict';
const PREFIX = 'sobhe-pwa-' + new URL(self.registration.scope).pathname + '-';
const CACHE_NAME = PREFIX + {{ version|tojson }};
const OFFLINE_URL = {{ offline_url|tojson }};
const PRECACHE = [OFFLINE_URL, ...{{ precache|tojson }}];
const PUBLIC_PATHS = new Set({{ public_paths|tojson }});

self.addEventListener('install', event => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE_NAME);
        await cache.addAll(PRECACHE.map(url => new Request(url, {cache: 'reload', credentials: 'omit'})));
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', event => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names.filter(name => name.startsWith(PREFIX) && name !== CACHE_NAME)
            .map(name => caches.delete(name)));
        // Update without reloading a page that may contain an unfinished form.
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== 'GET' || url.origin !== self.location.origin) return;
    if (request.mode === 'navigate') {
        // Reports, account pages, tokens, attachments, and API data stay on the network.
        event.respondWith(fetch(request).catch(async () => {
            const cache = await caches.open(CACHE_NAME);
            return await cache.match(OFFLINE_URL) || Response.error();
        }));
        return;
    }
    if (!PUBLIC_PATHS.has(url.pathname)) return;
    event.respondWith((async () => {
        const cache = await caches.open(CACHE_NAME);
        const cached = await cache.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        const control = response.headers.get('Cache-Control') || '';
        if (response.ok && response.type === 'basic' && !response.redirected && !/private|no-store/i.test(control)) {
            event.waitUntil(cache.put(request, response.clone()).catch(() => {}));
        }
        return response;
    })());
});
