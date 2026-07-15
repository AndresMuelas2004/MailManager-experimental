// LOCKSTEP with backend api/services/image_proxy_signing.py::SENTINEL_PREFIX.
// The incoming-email sanitizer bakes remote image URLs as
// ``https://mm-image-proxy.invalid/img?u=…&s=…`` — a fail-closed host (``.invalid``
// never resolves) so an unresolved URL breaks the image instead of leaking the
// user's IP to the sender. The frontend swaps this prefix for the real proxy path.
export const IMAGE_PROXY_SENTINEL_PREFIX = 'https://mm-image-proxy.invalid/img';

/**
 * Rewrite the sentinel image-proxy prefix baked into a sanitized email body to
 * the backend's real image-proxy path, preserving the ``?u=…&s=…`` query string.
 *
 * Only the prefix (everything up to ``/img``, before the ``?``) is replaced, so
 * an HTML-escaped ``&amp;`` between the query params survives untouched — the
 * browser decodes it when it loads the ``<img>``. Pure: ``proxyBase`` is passed
 * in rather than read from env so ``lib/`` stays free of app/environment state.
 */
export function resolveImageProxyUrls(html: string, proxyBase: string): string {
  return html.split(IMAGE_PROXY_SENTINEL_PREFIX).join(proxyBase);
}
