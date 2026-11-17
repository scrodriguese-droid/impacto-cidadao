/* ImpactoCidadão — service worker.
 * Objetivo: o site abrir rápido e funcionar offline (o que já foi visto), mesmo com o servidor gratuito "dormindo".
 * Estratégia: rede primeiro; se a rede demorar mais que ESPERA_MS (servidor acordando) ou falhar, usa a cópia guardada.
 * Só guarda requisições GET. Nunca guarda POST nem rotas do editor. Para forçar renovação geral, mude VERSAO. */
const VERSAO = 'ic-v1';
const ESPERA_MS = 4000;
const PAGINAS = 'ic-paginas-' + VERSAO;
const EXTERNOS = 'ic-externos-' + VERSAO;

self.addEventListener('install', (e) => { e.waitUntil(self.skipWaiting()); });
self.addEventListener('activate', (e) => {
    e.waitUntil((async () => {
        for (const k of await caches.keys()) if (!k.endsWith(VERSAO)) await caches.delete(k);
        await self.clients.claim();
    })());
});

const guardavel = (url) => !/\/api\/(editor|admin|conta|login)/i.test(url.pathname);

async function redePrimeiro(req, nome) {
    const cache = await caches.open(nome);
    const rede = fetch(req).then((r) => { if (r.ok) cache.put(req, r.clone()); return r; });
    const copia = await cache.match(req, { ignoreSearch: false });
    if (!copia) return rede;
    rede.catch(() => {}); // evita erro não tratado se a rede falhar depois de servirmos a cópia
    const limite = new Promise((res) => setTimeout(() => res(null), ESPERA_MS));
    try { return (await Promise.race([rede, limite])) || copia; } catch (_) { return copia; }
}

async function revalidar(req, nome) {
    const cache = await caches.open(nome);
    const copia = await cache.match(req);
    const rede = fetch(req).then((r) => { if (r.ok || r.type === 'opaque') cache.put(req, r.clone()); return r; }).catch(() => copia);
    return copia || rede;
}

self.addEventListener('fetch', (e) => {
    const req = e.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    if (!/^https?:$/.test(url.protocol)) return;
    if (url.origin === self.location.origin) {
        if (!guardavel(url)) return;
        e.respondWith(redePrimeiro(req, PAGINAS));
    } else if (/cdn\.tailwindcss\.com|fonts\.(googleapis|gstatic)\.com|cdnjs\.cloudflare\.com/.test(url.hostname)) {
        e.respondWith(revalidar(req, EXTERNOS));
    }
});
