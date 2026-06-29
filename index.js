export default {
  async fetch(request, env, ctx) {
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    const url = new URL(request.url);

    if (url.pathname !== '/vk/exchange-code') {
      return new Response('Not Found', { status: 404 });
    }

    const originUrl = 'http://89.108.78.99/vk/exchange-code';

    try {
      const response = await fetch(originUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: request.body,
      });

      const body = await response.text();

      return new Response(body, {
        status: response.status,
        headers: {
          'Content-Type': 'application/json',
          ...corsHeaders,
        },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: 'Origin unreachable', details: err.message }), {
        status: 502,
        headers: {
          'Content-Type': 'application/json',
          ...corsHeaders,
        },
      });
    }
  },
};
