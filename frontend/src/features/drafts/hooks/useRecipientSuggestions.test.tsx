/**
 * Integration tests for ``useRecipientSuggestions``.
 *
 * MSW intercepts the HTTP boundary; the real endpoint function, schema
 * validation and React Query cache all run. We assert the length gating
 * (no fetch under 2 chars), the happy-path content, and the silent
 * degradation to ``[]`` on error.
 *
 * The test query client uses ``retry: false`` / ``staleTime: 0`` (from
 * ``createTestQueryClient``), so these tests exercise gating + content +
 * degradation, NOT the production 30 s cache or ``retry: 1`` policy —
 * mirroring how ``useEmailList`` is tested.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import useRecipientSuggestions from './useRecipientSuggestions';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';

function wrapper({ children }: { children: ReactNode }) {
  const client = createTestQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  server.resetHandlers();
});

describe('useRecipientSuggestions', () => {
  it('does not fetch and returns [] when the query is shorter than 2 chars', async () => {
    let calls = 0;
    server.use(
      http.get(`${API_BASE}/contacts/suggestions`, () => {
        calls += 1;
        return HttpResponse.json([{ email: 'amparo@ejemplo.com', name: 'Amparo' }]);
      }),
    );

    const { result } = renderHook(() => useRecipientSuggestions('a'), { wrapper });

    // Give any (erroneous) fetch a chance to fire and settle.
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.suggestions).toEqual([]);
    expect(calls).toBe(0);
  });

  it('returns the handler payload once the query reaches 2 chars', async () => {
    server.use(
      http.get(`${API_BASE}/contacts/suggestions`, () =>
        HttpResponse.json([
          { email: 'amparo@ejemplo.com', name: 'Amparo López' },
          { email: 'soporte@empresa.com', name: null },
        ]),
      ),
    );

    const { result } = renderHook(() => useRecipientSuggestions('am'), { wrapper });

    await waitFor(() => expect(result.current.suggestions).toHaveLength(2));
    expect(result.current.suggestions[0]).toEqual({
      email: 'amparo@ejemplo.com',
      name: 'Amparo López',
    });
    expect(result.current.suggestions[1]).toEqual({ email: 'soporte@empresa.com', name: null });
  });

  it('sends the query as ?q= and a limit on the wire', async () => {
    let seenQ: string | null = null;
    let seenLimit: string | null = null;
    server.use(
      http.get(`${API_BASE}/contacts/suggestions`, ({ request }) => {
        const url = new URL(request.url);
        seenQ = url.searchParams.get('q');
        seenLimit = url.searchParams.get('limit');
        return HttpResponse.json([]);
      }),
    );

    const { result } = renderHook(() => useRecipientSuggestions('amp'), { wrapper });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(seenQ).toBe('amp');
    expect(seenLimit).toBe('8');
  });

  it('degrades silently to [] when the endpoint errors (no throw)', async () => {
    server.use(
      http.get(`${API_BASE}/contacts/suggestions`, () =>
        HttpResponse.json({ code: 'recipient_suggestions_error' }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useRecipientSuggestions('am'), { wrapper });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.suggestions).toEqual([]);
  });
});
