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

// ``color-scheme:light`` (here AND as inline style on the <iframe> element in
// both viewers) pins the email document to light rendering: per CSS
// color-adjust, a nested browsing context whose embedder's used color-scheme
// differs from the UA preference inherits the embedder's, so the sender's
// ``@media (prefers-color-scheme: dark)`` rules never fire even when the OS
// is in dark mode. The backend sanitizer also strips those media queries
// (Gmail parity) — this is the layer that protects already-cached bodies.
//
// Deliberately NO ``img{max-width:100%}`` reset: inside an auto-layout table
// cell (how real templates lay out logos) a percentage max-width makes the
// image's min-content width 0 and the cell can collapse to 0px — Amazon.es's
// header logo vanished exactly this way. Gmail web does not constrain email
// images either; wider-than-viewport content scrolls via ``overflow:auto``.
export function wrapHtmlEmail(html: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>html{color-scheme:light}html,body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#18181b;background:#fff;overflow:auto}body{padding:16px}</style></head><body>${html}</body></html>`;
}
