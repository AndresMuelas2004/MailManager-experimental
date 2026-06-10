/**
 * Unit-ish tests for ``useDraftPersistence``.
 *
 * We cover only the new ``ensureProviderDraftId`` helper introduced for
 * the silent-bootstrap flow (drag-and-drop attachments in
 * ``new_email`` / ``new_draft``). The legacy ``persistDraft`` /
 * ``saveDraftNow`` / ``sendDraftNow`` / ``sendEmailNow`` paths are
 * exercised end-to-end through ``useDraftComposer`` integration tests.
 *
 * MSW intercepts at the network boundary; the real ``createDraft``
 * endpoint wrapper, ``request<T>()`` and Zod schema validation all run
 * unmocked — the only synthesized layer is the HTTP response.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '../../../test/msw/server';
import useDraftPersistence from './useDraftPersistence';

const API_BASE = 'http://localhost:8000';

const sampleDraftPayload = {
  to_recipients: [],
  cc_recipients: [],
  bcc_recipients: [],
  subject: '',
  body: '',
};

function draftOutFixture(providerDraftId: string) {
  return {
    provider_draft_id: providerDraftId,
    account_id: 'acc_1',
    to_recipients: [],
    cc_recipients: [],
    bcc_recipients: [],
    subject: '',
    body: '',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    attachments: [],
  };
}

describe('useDraftPersistence — ensureProviderDraftId', () => {
  it('creates a draft on the provider when no id is cached and returns it', async () => {
    let createCallCount = 0;
    server.use(
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/acc_1/drafts`, () => {
        createCallCount += 1;
        return HttpResponse.json(draftOutFixture('drf_new'));
      }),
    );

    const { result } = renderHook(() => useDraftPersistence());

    let id = '';
    await act(async () => {
      id = await result.current.ensureProviderDraftId('mb_1', 'acc_1', sampleDraftPayload);
    });

    expect(id).toBe('drf_new');
    expect(createCallCount).toBe(1);
    // The id is now cached in the ref, so a follow-up persistDraft would
    // route through the update path — verified in the next test.
  });

  it('returns the cached id without calling the provider when one already exists', async () => {
    let createCallCount = 0;
    server.use(
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/acc_1/drafts`, () => {
        createCallCount += 1;
        return HttpResponse.json(draftOutFixture('drf_first'));
      }),
    );

    const { result } = renderHook(() => useDraftPersistence());

    // First call — creates and caches.
    let id1 = '';
    await act(async () => {
      id1 = await result.current.ensureProviderDraftId('mb_1', 'acc_1', sampleDraftPayload);
    });
    expect(id1).toBe('drf_first');
    expect(createCallCount).toBe(1);

    // Second call — short-circuits to the cached id, no extra HTTP traffic.
    let id2 = '';
    await act(async () => {
      id2 = await result.current.ensureProviderDraftId('mb_1', 'acc_1', sampleDraftPayload);
    });
    expect(id2).toBe('drf_first');
    expect(createCallCount).toBe(1);
  });

  it('records the error and re-throws when the provider rejects the create', async () => {
    server.use(
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/acc_1/drafts`, () =>
        HttpResponse.json(
          { error: { code: 'provider_unavailable', message: 'provider down' } },
          { status: 503 },
        ),
      ),
    );

    const { result } = renderHook(() => useDraftPersistence());

    let thrown: unknown = null;
    await act(async () => {
      try {
        await result.current.ensureProviderDraftId('mb_1', 'acc_1', sampleDraftPayload);
      } catch (e) {
        thrown = e;
      }
    });

    expect(thrown).not.toBeNull();
    await waitFor(() => {
      expect(result.current.error?.code).toBe('provider_unavailable');
    });
  });
});

describe('useDraftPersistence — body-too-large 422 translation', () => {
  it('rewrites a bare FastAPI 422 (no envelope) into a readable Spanish body error', async () => {
    // A body-size 422 is a Pydantic validation error: its payload is
    // ``{ detail: [...] }`` (NOT the ``{ error: { code, message } }``
    // envelope), so ``toApiError`` falls back to ``http_error`` / 422 and
    // ``toComposerError`` replaces it with the ``body_too_large`` message.
    // This path is only reachable when the client-side ``bodyError`` gate is
    // bypassed, but the Spanish message contract is unconditional.
    server.use(
      http.post(`${API_BASE}/mailboxes/mb_1/emails/send`, () =>
        HttpResponse.json(
          {
            detail: [
              {
                type: 'string_too_long',
                loc: ['body', 'body'],
                msg: 'String should have at most 1000000 characters',
              },
            ],
          },
          { status: 422 },
        ),
      ),
    );

    const { result } = renderHook(() => useDraftPersistence());

    let ok = true;
    await act(async () => {
      ok = await result.current.sendEmailNow(
        'mb_1',
        'acc_1',
        ['to@example.com'],
        'Subject',
        'x'.repeat(1_000_001),
      );
    });

    expect(ok).toBe(false);
    await waitFor(() => {
      expect(result.current.error).toEqual({
        message: 'El mensaje es demasiado grande. Reduce su tamaño.',
        code: 'body_too_large',
      });
    });
  });
});
