import { buildImageProxyBaseUrl, getEmailContent } from '../../../api/endpoints/emails';
import { resolveImageProxyUrls } from '../../../lib/imageProxy';
import type { EmailContentOut } from '../../../api/types/dto';

// Single source of truth for the email-content query's key + cache policy,
// shared by ``useEmailContent`` (read on open) and ``useEmailContentPrefetch``
// (background warm). Both MUST use these exact options so the warmed cache entry
// is the same one the viewer reads — a divergent key/staleTime would make the
// prefetch useless. Not a hook (no ``use`` prefix, returns an options object)
// but hook-adjacent; the feature only allows pages/hooks/components, so it lives
// in ``hooks/``.
//
// ``staleTime: Infinity``: the sanitized body is immutable and its signed proxy
// URLs are stable, so reopening never revalidates — cache hit, no spinner, no
// request. ``gcTime`` (30 min) must outlive the gap between prefetch and open.
//
// Argument-order footgun: this takes (mailboxId, accountId, providerMessageId)
// but ``getEmailContent`` takes (mailboxId, providerMessageId, accountId) — the
// last two are swapped inside ``queryFn``. Keep both in lockstep.
//
// The ``queryFn`` also rewrites the sanitized body's fail-closed sentinel image
// URLs to the absolute image-proxy path (privacy — the iframe's <img> loads
// through the backend, not the sender). This response-shaping lives here, not in
// the ``getEmailContent`` endpoint wrapper, which stays a thin ``request()`` call
// per api/CLAUDE.md §4.1; the proxy base comes from ``api/endpoints`` (never
// ``api/client/http`` directly) to respect the feature import boundary (§8).
export function emailContentQueryOptions(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
) {
  return {
    queryKey: ['email-content', mailboxId, accountId, providerMessageId] as const,
    queryFn: async ({ signal }: { signal: AbortSignal }): Promise<EmailContentOut> => {
      const result = await getEmailContent(mailboxId, providerMessageId, accountId, signal);
      if (!result.html_body) return result;
      return {
        ...result,
        html_body: resolveImageProxyUrls(result.html_body, buildImageProxyBaseUrl()),
      };
    },
    staleTime: Infinity,
    gcTime: 30 * 60_000,
  };
}
