/**
 * Minimal API client: JSON requests + Server-Sent-Events streaming.
 * No external dependencies; uses fetch and ReadableStream.
 */

export class ApiError extends Error {
  constructor(code, message, status = 0) {
    super(message || code || 'error');
    this.code = code;
    this.status = status;
  }
}

async function parse(response) {
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (err) {
    data = null;
  }
  if (!response.ok) {
    const code = (data && data.error) || `HTTP_${response.status}`;
    const message = (data && data.message) || `HTTP ${response.status}`;
    throw new ApiError(code, message, response.status);
  }
  return data;
}

export const api = {
  async get(path) {
    return parse(await fetch(path, { headers: { Accept: 'application/json' } }));
  },
  async post(path, body) {
    return parse(
      await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body ?? {}),
      })
    );
  },
  async put(path, body) {
    return parse(
      await fetch(path, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body ?? {}),
      })
    );
  },
  async del(path) {
    return parse(await fetch(path, { method: 'DELETE', headers: { Accept: 'application/json' } }));
  },

  /**
   * POST + SSE. `handlers.onEvent(event)` for every JSON event.
   * Returns when the stream ends.
   */
  async stream(path, body, handlers = {}) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body ?? {}),
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => null);
      throw new ApiError((data && data.error) || `HTTP_${response.status}`,
        (data && data.message) || response.statusText, response.status);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try {
            handlers.onEvent && handlers.onEvent(JSON.parse(raw));
          } catch (err) {
            console.warn('Некорректное событие', err, raw);
          }
        }
      }
    }
  },
};

export const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function formatTime(seconds) {
  const d = new Date(seconds * 1000);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}
