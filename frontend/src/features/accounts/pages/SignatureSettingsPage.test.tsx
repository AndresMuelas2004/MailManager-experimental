/**
 * Integration test for the per-account email-signature settings page.
 *
 * The full feature slice runs unmocked — ``useAccountSignatures`` (its
 * ``useQuery`` read + ``useMutation`` write + ``['accounts', mailboxId]``
 * invalidation), the ``request<T>()`` client, schema validation, and the
 * real ``RichTextEditor`` (TipTap/ProseMirror, geometry polyfilled in
 * ``src/test/setup.ts``). Only the HTTP boundary is synthesised via MSW.
 *
 * The page reads ``useParams<{ mailboxId }>()``, so it is mounted under a
 * ``:mailboxId`` route — without it the query is disabled (``enabled:
 * !!mailboxId``) and never fires.
 */

import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { Route, Routes } from 'react-router-dom';

import SignatureSettingsPage from './SignatureSettingsPage';
import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';
const MAILBOX_ID = 'mb_1';

// The page renders Spanish copy through the real I18nProvider; pin the
// language so the fixed label/heading lookups resolve.
pinTestLang('es');

function accountFixture(overrides: Record<string, unknown> = {}) {
  return {
    account_id: 'acc_1',
    mailbox_id: MAILBOX_ID,
    provider: 'gmail',
    display_label: 'Gmail',
    config: {},
    email_address: 'me@example.com',
    signature_html: null,
    ...overrides,
  };
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/m/:mailboxId/settings/signature" element={<SignatureSettingsPage />} />
    </Routes>,
    { initialEntries: [`/m/${MAILBOX_ID}/settings/signature`] },
  );
}

/** Wait a tick so the deferred ``useEditor`` (immediatelyRender: false) mounts. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('SignatureSettingsPage', () => {
  it('shows the empty state when the mailbox has no accounts', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts`, () => HttpResponse.json([])),
    );

    renderPage();

    expect(
      await screen.findByText('No tienes cuentas conectadas en esta bandeja.'),
    ).toBeInTheDocument();
  });

  it('renders one editor per account, preloaded with the persisted signature', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts`, () =>
        HttpResponse.json([accountFixture({ signature_html: '<p>Persisted sig</p>' })]),
      ),
    );

    renderPage();
    await settle();

    const editable = await screen.findByLabelText(/Editor de firma/);
    // ProseMirror parses the seeded HTML into its restricted schema and
    // renders it on the contenteditable surface.
    expect(editable.textContent).toContain('Persisted sig');
  });

  it('saves the typed signature via PATCH with the entered HTML', async () => {
    // Capture into a typed array (not a mutated ``let``) so the post-await
    // assertion narrows correctly under strict TS.
    const patchedSignatures: unknown[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts`, () =>
        HttpResponse.json([accountFixture()]),
      ),
      http.patch(
        `${API_BASE}/mailboxes/${MAILBOX_ID}/accounts/:accountId`,
        async ({ request, params }) => {
          const body = (await request.json()) as Record<string, unknown>;
          patchedSignatures.push(body.signature_html);
          return HttpResponse.json(
            accountFixture({
              account_id: String(params.accountId),
              signature_html: typeof body.signature_html === 'string' ? body.signature_html : null,
            }),
          );
        },
      ),
    );

    const user = userEvent.setup();
    renderPage();
    await settle();

    const editable = await screen.findByLabelText(/Editor de firma/);
    await user.click(editable);
    // A single character is the reliable jsdom/ProseMirror typing unit (the
    // RichTextEditor suite asserts on structure, not exact multi-char round
    // trips, for the same reason). We assert the editor serialised the typed
    // content into a paragraph and that the PATCH carried that HTML — not the
    // exact keystrokes — so the test pins the save contract, not the editor's
    // keystroke fidelity.
    await user.keyboard('Z');

    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    // The PATCH carried the entered signature as wrapped HTML…
    await waitFor(() => expect(patchedSignatures).toHaveLength(1));
    const sentSignature = String(patchedSignatures[0]);
    expect(sentSignature).toContain('Z');
    expect(sentSignature).toContain('<p>');
    // …and the success confirmation appears.
    expect(await screen.findByText('Firma guardada.')).toBeInTheDocument();
  });

  it('clears the signature with an empty string when the editor is emptied and saved', async () => {
    const patchedSignatures: unknown[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts`, () =>
        HttpResponse.json([accountFixture({ signature_html: '<p>old</p>' })]),
      ),
      http.patch(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts/:accountId`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        patchedSignatures.push(body.signature_html);
        return HttpResponse.json(accountFixture({ signature_html: '' }));
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await settle();

    const editable = await screen.findByLabelText(/Editor de firma/);
    await user.click(editable);
    // Select all the seeded content and delete it: the editor emits '' (the
    // ``normalizeEmpty`` of a now-empty document).
    await user.keyboard('{Control>}a{/Control}{Backspace}');

    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    await waitFor(() => expect(patchedSignatures).toHaveLength(1));
    expect(patchedSignatures[0]).toBe('');
  });

  it('surfaces the error banner when the PATCH fails', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts`, () =>
        HttpResponse.json([accountFixture()]),
      ),
      http.patch(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts/:accountId`, () =>
        HttpResponse.json(
          { error: { code: 'account_operation_error', message: 'Failed to update account.' } },
          { status: 500 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderPage();
    await settle();

    const editable = await screen.findByLabelText(/Editor de firma/);
    await user.click(editable);
    await user.keyboard('x');
    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    // The hook translates the ApiError through ``toUiError``; the page renders
    // its message in the banner.
    expect(await screen.findByText('Failed to update account.')).toBeInTheDocument();
  });

  it('blocks saving and shows the too-long hint when the signature exceeds the cap', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/${MAILBOX_ID}/accounts`, () =>
        HttpResponse.json([accountFixture({ signature_html: `<p>${'a'.repeat(10_001)}</p>` })]),
      ),
    );

    renderPage();
    await settle();

    // The seeded value already exceeds 10_000 chars, so the client-side guard
    // disables the button and shows the hint (mirrors the backend cap).
    expect(await screen.findByText('La firma es demasiado larga.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled();
  });
});
