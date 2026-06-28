// Shared HTML-wrapping helpers for rendering a sanitised email body inside a
// sandboxed iframe. Kept feature-local (not in ``lib/``) because wrapping "an
// email body for an iframe" carries domain intent and both consumers
// (``EmailViewer`` and ``ConversationMessageBody``) live in the ``emails``
// feature — ``lib/`` is reserved for domain-agnostic helpers used across
// features (lib/CLAUDE.md §3.2).
//
// The HTML passed to ``wrapHtmlEmail`` is already sanitised by the backend
// rendering pipeline; the iframe keeps a strict sandbox (no allow-same-origin,
// no allow-scripts) at the call site as defence in depth.

export function escapeHtml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// A body that is null, empty, or whitespace-only (a stray "\n"/"\r\n" surviving
// sanitisation) is not usable content. Callers must fall through to the "no
// content" notice instead of mounting an iframe around blank text.
export function hasRenderableBody(body: string | null | undefined): body is string {
  return body != null && body.trim() !== '';
}

export function wrapPlainText(text: string): string {
  const escaped = escapeHtml(text);
  return `<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;padding:16px;font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:14px;color:#18181b"><pre style="white-space:pre-wrap;margin:0;font-family:inherit;font-size:inherit">${escaped}</pre></body></html>`;
}

export function wrapHtmlEmail(html: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>html,body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#18181b;background:#fff;overflow:auto}body{padding:16px}img{max-width:100%;height:auto}</style></head><body>${html}</body></html>`;
}
