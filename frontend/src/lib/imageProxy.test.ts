/**
 * Unit tests for the ``imageProxy`` sentinel-URL resolver.
 *
 * Pure string rewrite: ``resolveImageProxyUrls`` swaps the fail-closed sentinel
 * prefix baked into a sanitized email body for the backend's real image-proxy
 * path, preserving the ``?u=…&s=…`` query string. Only the prefix is replaced,
 * so an HTML-escaped ``&amp;`` between the params survives untouched.
 */

import { describe, expect, it } from 'vitest';

import { IMAGE_PROXY_SENTINEL_PREFIX, resolveImageProxyUrls } from './imageProxy';

const PROXY_BASE = 'http://localhost:8000/image-proxy';

describe('resolveImageProxyUrls', () => {
  it('swaps the sentinel prefix for the proxy base, preserving the query string', () => {
    const html = `<img src="${IMAGE_PROXY_SENTINEL_PREFIX}?u=ABC&amp;s=DEF">`;
    expect(resolveImageProxyUrls(html, PROXY_BASE)).toBe(
      `<img src="${PROXY_BASE}?u=ABC&amp;s=DEF">`,
    );
  });

  it('rewrites every occurrence (a newsletter references many images)', () => {
    const html =
      `<img src="${IMAGE_PROXY_SENTINEL_PREFIX}?u=A&amp;s=1">` +
      `<td background="${IMAGE_PROXY_SENTINEL_PREFIX}?u=B&amp;s=2">.</td>`;
    const out = resolveImageProxyUrls(html, PROXY_BASE);
    expect(out).not.toContain(IMAGE_PROXY_SENTINEL_PREFIX);
    expect(out).toContain(`${PROXY_BASE}?u=A&amp;s=1`);
    expect(out).toContain(`${PROXY_BASE}?u=B&amp;s=2`);
  });

  it('leaves a body without the sentinel untouched', () => {
    const html = '<p>hola</p><img src="cid:logo@x"><a href="https://example.com">x</a>';
    expect(resolveImageProxyUrls(html, PROXY_BASE)).toBe(html);
  });

  it('does not touch cid: or data: image sources', () => {
    const html =
      `<img src="cid:x">` +
      `<img src="${IMAGE_PROXY_SENTINEL_PREFIX}?u=A&amp;s=1">` +
      `<img src="data:image/png;base64,AAA">`;
    const out = resolveImageProxyUrls(html, PROXY_BASE);
    expect(out).toContain('src="cid:x"');
    expect(out).toContain('src="data:image/png;base64,AAA"');
    expect(out).toContain(`src="${PROXY_BASE}?u=A&amp;s=1"`);
  });

  it('returns the input unchanged when there is no sentinel at all', () => {
    const html = '<p>sin imagenes remotas</p>';
    expect(resolveImageProxyUrls(html, PROXY_BASE)).toBe(html);
  });
});
