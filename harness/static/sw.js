// Makes the workbench installable as its own app window (2026-10-28). Network first: the page
// always loads the current version from the local server, and the cached copy is only a fallback
// that says the workbench is not running. API and MCP calls are never cached.
const CACHE = 'workbench-shell-v1';
const SHELL = ['/', '/style.css', '/theme.js', '/icon-192.png'];
self.addEventListener('install', e => {e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()))});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/') || url.pathname === '/mcp') return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && SHELL.includes(url.pathname)) {const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy))}
    return r;
  }).catch(() => caches.match(e.request, {ignoreSearch: true}).then(r => r || new Response(
    '<!doctype html><meta charset="utf-8"><title>Prompt Workbench</title><body style="font:16px system-ui;padding:40px">' +
    '<h1>The workbench is not running</h1><p>Start it with <b>start.cmd</b>, then reload this window.</p>',
    {headers: {'Content-Type': 'text/html; charset=utf-8'}}))));
});
