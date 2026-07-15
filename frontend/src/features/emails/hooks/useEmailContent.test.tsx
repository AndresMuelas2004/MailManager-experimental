/**
 * Integration tests for ``useEmailContent``.
 *
 * MSW intercepts the HTTP boundary; the real endpoint (schema validation +
 * image-proxy URL resolution) and the React Query cache all run. The hook now
 * reads the body from the in-memory TanStack Query cache with
 * ``staleTime: Infinity`` (via ``emailContentQueryOptions``), so reopening the
 * same email is a cache hit with no second request. This is verified in a
 * mono-message context (not through a thread-grouped page) so the assertion is
 * not confounded by Outlook's duplicate-id trap.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import useEmailContent from './useEmailContent';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';
const TARGET = { account_id: 'a_1', provider_message_id: 'm_1' };

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

afterEach(() => {
  server.resetHandlers();
});

describe('useEmailContent', () => {
  it('fetches the sanitized body on a cache miss and exposes it', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails/m_1/content`, () =>
        HttpResponse.json({ html_body: '<p>cuerpo</p>', text_body: 'cuerpo', attachments: [] }),
      ),
    );

    const { result } = renderHook(() => useEmailContent('mb_1', TARGET), {
      wrapper: makeWrapper(createTestQueryClient()),
    });

    await waitFor(() => expect(result.current.content).not.toBeNull());
    expect(result.current.content?.html_body).toBe('<p>cuerpo</p>');
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('serves a reopened email from cache without a second request', async () => {
    let contentRequests = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails/m_1/content`, () => {
        contentRequests += 1;
        return HttpResponse.json({
          html_body: '<p>cuerpo</p>',
          text_body: 'cuerpo',
          attachments: [],
        });
      }),
    );

    // A single shared client so the warmed cache entry survives the remount.
    const wrapper = makeWrapper(createTestQueryClient());

    const first = renderHook(() => useEmailContent('mb_1', TARGET), { wrapper });
    await waitFor(() => expect(first.result.current.content).not.toBeNull());
    expect(contentRequests).toBe(1);
    first.unmount();

    // Reopening the same email is a cache hit (staleTime Infinity), so no second
    // network request fires and the body is available without a spinner.
    const second = renderHook(() => useEmailContent('mb_1', TARGET), { wrapper });
    await waitFor(() => expect(second.result.current.content).not.toBeNull());
    expect(second.result.current.loading).toBe(false);
    expect(contentRequests).toBe(1);
  });

  it('surfaces a translated UiError on a failed fetch', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails/m_1/content`, () =>
        HttpResponse.json(
          { error: { code: 'email_not_found', message: 'No existe' } },
          { status: 404 },
        ),
      ),
    );

    const { result } = renderHook(() => useEmailContent('mb_1', TARGET), {
      wrapper: makeWrapper(createTestQueryClient()),
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.code).toBe('email_not_found');
    expect(result.current.content).toBeNull();
  });
});
