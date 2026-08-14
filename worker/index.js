// Запасной путь: прокси Cloudflare на случай, если браузер не принимает
// сертификат сервера. В норме мини-приложение ходит на сервер напрямую,
// и воркер не нужен — разворачивать его только если прямой запрос падает.
//
// ORIGIN держим на http, потому что смысл прокси как раз в том, чтобы
// подменить проблемный TLS сервера сертификатом Cloudflare.

const ORIGIN = 'http://89.108.78.99';
const ALLOWED_PATHS = [
  '/vk/token',
  '/vk/queue',
  '/vk/queue/ack',
  '/vk/status',
  '/vk/remove-user',
  '/vk/ban-user',
];

export default {
  async fetch(request) {
    const cors = {
      'Access-Control-Allow-Origin': 'https://hawkey-prog.github.io',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Secret, X-Bot-Secret',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }

    const url = new URL(request.url);
    if (!ALLOWED_PATHS.includes(url.pathname)) {
      return new Response(JSON.stringify({ error: 'Not Found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    }

    // Секреты живут в заголовках — пробрасываем их как есть, ничего не логируя.
    const headers = { 'Content-Type': 'application/json' };
    for (const name of ['X-Admin-Secret', 'X-Bot-Secret']) {
      const value = request.headers.get(name);
      if (value) headers[name] = value;
    }

    try {
      const response = await fetch(ORIGIN + url.pathname + url.search, {
        method: request.method,
        headers,
        body: request.method === 'POST' ? await request.text() : undefined,
      });

      return new Response(await response.text(), {
        status: response.status,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: 'Origin unreachable', details: err.message }), {
        status: 502,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    }
  },
};
