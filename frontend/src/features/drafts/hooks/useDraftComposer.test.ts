/**
 * Behavioural tests for ``useDraftComposer`` after the silent-bootstrap
 * change for drag-and-drop attachments (D-25 follow-up). The new
 * surface area is:
 *
 *  - ``addAttachmentFiles`` triggers a silent ``createDraft`` the first
 *    time it runs in ``new_email`` / ``new_draft``.
 *  - A concurrency lock prevents two near-simultaneous drops from
 *    bootstrapping twice.
 *  - Files dropped before the account list resolves are queued and
 *    drained when ``form.accountId`` becomes non-empty.
 *  - ``handleSendEmail`` redirects to ``sendDraftNow`` once a silent
 *    draft exists (so the lazy-pushed attachments travel with the send).
 *  - ``closeWithX`` opens the discard dialog for ``new_email`` once the
 *    silent draft exists.
 *  - ``confirmCloseDiscard`` deletes the silent draft on the provider.
 *  - ``accountSelectorLocked`` flips on once a silent draft exists.
 *  - A failing ``createDraft`` surfaces the error and skips the upload.
 *
 * MSW intercepts both ``fetch`` (regular endpoints) and ``XMLHttpRequest``
 * (attachment upload via ``requestUploadWithProgress``) at the network
 * boundary — the entire endpoint and HTTP-client layer runs unmocked.
 */

import {
  act,
  renderHook as rtlRenderHook,
  waitFor,
  type RenderHookOptions,
} from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '../../../test/msw/server';
import { I18nProvider } from '../../../lib/i18n';
import useDraftComposer from './useDraftComposer';

const API_BASE = 'http://localhost:8000';

// ``useDraftComposer`` now reads composer error copy through ``useTranslation``,
// so every ``renderHook`` must run inside the real I18nProvider. A local
// ``renderHook`` injects it without touching the dozens of call sites.
function renderHook<R, P>(
  render: (props: P) => R,
  options?: Omit<RenderHookOptions<P>, 'wrapper'>,
) {
  return rtlRenderHook(render, { wrapper: I18nProvider, ...options });
}

function gmailAccountFixture(accountId = 'acc_1') {
  return {
    account_id: accountId,
    mailbox_id: 'mb_1',
    provider: 'gmail',
    display_label: 'Gmail',
    config: {},
    email_address: 'tester@example.com',
    // Widen from the literal ``null`` so signature-insertion specs can build a
    // signed variant (``signedAccount``) without TS pinning the field to null.
    signature_html: null as string | null,
  };
}

function draftOutFixture(providerDraftId = 'drf_1', accountId = 'acc_1') {
  return {
    provider_draft_id: providerDraftId,
    account_id: accountId,
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

function draftAttachmentResponseFixture(callIndex = 1, filename = 'a.pdf', size = 100) {
  // Each call must return a distinct uuid so the chip-mapping side of
  // ``useComposerAttachments.addFiles`` can update by id without collisions.
  // ``callIndex`` lets the handler thread its monotonically-increasing
  // counter into the synthetic uuid suffix.
  const suffix = String(callIndex).padStart(12, '0');
  return {
    draft_attachment_id: `11111111-1111-4000-a000-${suffix}`,
    filename,
    mime_type: 'application/pdf',
    size,
    position: callIndex - 1,
    provider_attachment_id: null,
  };
}

function makeFile(name: string, sizeBytes: number, mime = 'application/pdf'): File {
  return new File([new Uint8Array(sizeBytes)], name, { type: mime });
}

/**
 * Stand-in for a deferred. The accounts handler awaits this promise
 * before responding, letting tests freeze the loadAccountsIfNeeded
 * step until they are ready to observe the queueing/drain behaviour.
 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * Install the typical happy-path handlers used by every bootstrap
 * test. Returns counters/payload captures the test can assert on.
 */
function installBootstrapHandlers(
  opts: {
    accounts?: ReturnType<typeof gmailAccountFixture>[];
    draftId?: string;
    delayedAccounts?: Promise<unknown> | null;
    attachmentResponseFn?: (n: number) => ReturnType<typeof draftAttachmentResponseFixture>;
  } = {},
) {
  const accounts = opts.accounts ?? [gmailAccountFixture()];
  const draftId = opts.draftId ?? 'drf_silent';
  const counters = {
    listAccounts: 0,
    createDraft: 0,
    addAttachment: 0,
    deleteDraft: 0,
    sendDraft: 0,
    sendEmail: 0,
    updateDraft: 0,
  };
  const captured: {
    createDraftBodies: unknown[];
    sendDraftPaths: string[];
    sendEmailBodies: unknown[];
    updateDraftBodies: unknown[];
    addAttachmentPaths: string[];
  } = {
    createDraftBodies: [],
    sendDraftPaths: [],
    sendEmailBodies: [],
    updateDraftBodies: [],
    addAttachmentPaths: [],
  };

  server.use(
    http.get(`${API_BASE}/mailboxes/mb_1/accounts`, async () => {
      counters.listAccounts += 1;
      if (opts.delayedAccounts) {
        await opts.delayedAccounts;
      }
      return HttpResponse.json(accounts);
    }),
    http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts`, async ({ request }) => {
      counters.createDraft += 1;
      captured.createDraftBodies.push(await request.json());
      return HttpResponse.json(draftOutFixture(draftId));
    }),
    http.post(
      `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/attachments`,
      ({ request }) => {
        counters.addAttachment += 1;
        captured.addAttachmentPaths.push(new URL(request.url).pathname);
        return HttpResponse.json(
          (opts.attachmentResponseFn ?? draftAttachmentResponseFixture)(counters.addAttachment),
        );
      },
    ),
    http.delete(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId`, () => {
      counters.deleteDraft += 1;
      return HttpResponse.json({ status: 'deleted' });
    }),
    http.post(
      `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/send`,
      ({ request }) => {
        counters.sendDraft += 1;
        captured.sendDraftPaths.push(new URL(request.url).pathname);
        return HttpResponse.json({
          provider_message_id: 'msg_sent',
          provider: 'gmail',
          status: 'sent',
        });
      },
    ),
    http.post(`${API_BASE}/mailboxes/mb_1/emails/send`, async ({ request }) => {
      counters.sendEmail += 1;
      captured.sendEmailBodies.push(await request.json());
      return HttpResponse.json({ status: 'sent' });
    }),
    http.patch(
      `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId`,
      async ({ request }) => {
        counters.updateDraft += 1;
        captured.updateDraftBodies.push(await request.json());
        return HttpResponse.json(draftOutFixture(draftId));
      },
    ),
  );

  return { counters, captured };
}

describe('useDraftComposer — silent bootstrap on attach', () => {
  it('creates a provider draft once and uploads the dropped file in new_email', async () => {
    const { counters, captured } = installBootstrapHandlers({ draftId: 'drf_silent_1' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });

    // Wait for the accounts to load and form.accountId to be set.
    await waitFor(() => {
      expect(result.current.accountId).toBe('acc_1');
    });

    const file = makeFile('drop.pdf', 1024);
    act(() => {
      result.current.addAttachmentFiles([file]);
    });

    // Bootstrap fires: a single createDraft, followed by an attachment upload.
    await waitFor(() => {
      expect(counters.createDraft).toBe(1);
      expect(counters.addAttachment).toBe(1);
    });

    // The createDraft body matches an empty payload — the silent draft is
    // bootstrapped without leaking any partially-typed content.
    expect(captured.createDraftBodies[0]).toEqual({
      to_recipients: [],
      cc_recipients: [],
      bcc_recipients: [],
      subject: '',
      body: '',
    });
    // And the attachment was POSTed against the just-created draft id.
    expect(captured.addAttachmentPaths[0]).toBe(
      '/mailboxes/mb_1/accounts/acc_1/drafts/drf_silent_1/attachments',
    );

    // Once uploaded, the chip flips to 'uploaded' and the silent draft
    // becomes "active" for the composer.
    await waitFor(() => {
      expect(result.current.attachmentChips).toHaveLength(1);
      expect(result.current.attachmentChips[0].status).toBe('uploaded');
    });
    expect(result.current.accountSelectorLocked).toBe(true);
  });

  it('creates a provider draft once in new_draft (same flow as new_email)', async () => {
    const { counters } = installBootstrapHandlers({ draftId: 'drf_silent_2' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewDraft();
    });

    await waitFor(() => {
      expect(result.current.accountId).toBe('acc_1');
      expect(result.current.mode).toBe('new_draft');
    });

    act(() => {
      result.current.addAttachmentFiles([makeFile('one.pdf', 1024)]);
    });

    await waitFor(() => {
      expect(counters.createDraft).toBe(1);
      expect(counters.addAttachment).toBe(1);
    });
  });

  it('does not bootstrap twice when two drops fire back-to-back (the lock works)', async () => {
    const { counters } = installBootstrapHandlers({ draftId: 'drf_locked' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => {
      expect(result.current.accountId).toBe('acc_1');
    });

    // Two synchronous drops in the same tick: the second invocation must
    // observe the in-flight create from the first and reuse its promise
    // (guarded by ``creatingDraftRef``). Both attachments still upload.
    act(() => {
      result.current.addAttachmentFiles([makeFile('a.pdf', 100)]);
      result.current.addAttachmentFiles([makeFile('b.pdf', 200)]);
    });

    await waitFor(() => {
      expect(counters.addAttachment).toBe(2);
    });

    expect(counters.createDraft).toBe(1);
    // Both uploads landed on the same silent draft id.
    const chips = result.current.attachmentChips;
    expect(chips).toHaveLength(2);
  });
});

describe('useDraftComposer — pendingFiles queue', () => {
  it('queues files added before accountId resolves and drains them on load', async () => {
    const accountsGate = deferred<void>();
    const { counters } = installBootstrapHandlers({
      delayedAccounts: accountsGate.promise,
      draftId: 'drf_after_drain',
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });

    // Mode is set, but accountId is still '' because the accounts request
    // is parked at ``accountsGate``.
    expect(result.current.mode).toBe('new_email');
    expect(result.current.accountId).toBe('');

    act(() => {
      result.current.addAttachmentFiles([makeFile('queued.pdf', 100)]);
    });

    // While the queue is full, no createDraft fires.
    expect(counters.createDraft).toBe(0);
    expect(counters.addAttachment).toBe(0);

    // Release the accounts response. Once accountId flips to 'acc_1',
    // the useEffect drains pendingFilesRef and the bootstrap kicks in.
    await act(async () => {
      accountsGate.resolve();
    });

    await waitFor(() => {
      expect(result.current.accountId).toBe('acc_1');
    });
    await waitFor(() => {
      expect(counters.createDraft).toBe(1);
      expect(counters.addAttachment).toBe(1);
    });
  });
});

describe('useDraftComposer — close clears refs', () => {
  it('closeWithX without dirty form/attachments resets pending and creating refs', async () => {
    const { counters } = installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // closeWithX with no chips and no content closes immediately.
    act(() => {
      result.current.closeWithX();
    });

    expect(result.current.open).toBe(false);
    expect(result.current.mode).toBeNull();
    expect(result.current.closeDialogOpen).toBe(false);

    // Reopening a fresh composer must NOT replay pending files from a
    // previous session, and must not reuse a stale creatingDraft promise
    // (no createDraft yet, no addAttachment yet).
    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    expect(counters.createDraft).toBe(0);
    expect(counters.addAttachment).toBe(0);
  });
});

describe('useDraftComposer — handleSendEmail redirection', () => {
  it('falls back to sendEmailNow in new_email when no silent draft exists', async () => {
    const { counters } = installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    act(() => {
      result.current.setTo('to@example.com');
    });

    await act(async () => {
      await result.current.handleSendEmail();
    });

    expect(counters.sendEmail).toBe(1);
    expect(counters.sendDraft).toBe(0);
    expect(counters.updateDraft).toBe(0);
  });

  it('redirects to sendDraftNow (not sendEmailNow) when a silent draft already exists', async () => {
    const { counters, captured } = installBootstrapHandlers({ draftId: 'drf_redir' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // Trigger silent bootstrap.
    act(() => {
      result.current.addAttachmentFiles([makeFile('attached.pdf', 100)]);
    });
    await waitFor(() => {
      expect(counters.addAttachment).toBe(1);
      expect(result.current.attachmentChips[0]?.status).toBe('uploaded');
    });

    act(() => {
      result.current.setTo('to@example.com');
      result.current.setSubject('Hello');
    });

    await act(async () => {
      await result.current.handleSendEmail();
    });

    // The redirect path: sendDraft + (forced) updateDraft, not sendEmail.
    expect(counters.sendEmail).toBe(0);
    expect(counters.sendDraft).toBe(1);
    expect(counters.updateDraft).toBe(1);
    // The forced payload must include the typed recipients/subject so
    // the provider draft reflects the latest form state before the send.
    expect(captured.updateDraftBodies[0]).toEqual({
      to_recipients: ['to@example.com'],
      cc_recipients: [],
      bcc_recipients: [],
      subject: 'Hello',
      body: '',
    });
    expect(captured.sendDraftPaths[0]).toBe('/mailboxes/mb_1/accounts/acc_1/drafts/drf_redir/send');
  });
});

describe('useDraftComposer — closeWithX after silent bootstrap', () => {
  it('opens the discard dialog when the user pressed X with chips and a silent draft', async () => {
    const { counters } = installBootstrapHandlers({ draftId: 'drf_close_dialog' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    act(() => {
      result.current.addAttachmentFiles([makeFile('keep.pdf', 100)]);
    });
    await waitFor(() => {
      expect(counters.addAttachment).toBe(1);
      expect(result.current.attachmentChips[0]?.status).toBe('uploaded');
    });

    act(() => {
      result.current.closeWithX();
    });

    // The dialog appears and the composer stays open until the user picks
    // Save / Discard / Cancel.
    expect(result.current.closeDialogOpen).toBe(true);
    expect(result.current.open).toBe(true);
  });

  it('confirmCloseDiscard deletes the silent draft on the provider after bootstrap', async () => {
    const { counters } = installBootstrapHandlers({ draftId: 'drf_to_delete' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    act(() => {
      result.current.addAttachmentFiles([makeFile('toss.pdf', 100)]);
    });
    await waitFor(() => {
      expect(counters.addAttachment).toBe(1);
      expect(result.current.attachmentChips[0]?.status).toBe('uploaded');
    });

    act(() => {
      result.current.closeWithX();
    });
    expect(result.current.closeDialogOpen).toBe(true);

    await act(async () => {
      await result.current.confirmCloseDiscard();
    });

    expect(counters.deleteDraft).toBe(1);
    expect(result.current.open).toBe(false);
    expect(result.current.mode).toBeNull();
  });
});

describe('useDraftComposer — accountSelectorLocked', () => {
  it('is false in fresh new_email and flips to true once a silent draft exists', async () => {
    const { counters } = installBootstrapHandlers({ draftId: 'drf_lock_flag' });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    expect(result.current.accountSelectorLocked).toBe(false);

    act(() => {
      result.current.addAttachmentFiles([makeFile('lock.pdf', 100)]);
    });
    await waitFor(() => {
      expect(counters.addAttachment).toBe(1);
    });

    expect(result.current.accountSelectorLocked).toBe(true);
  });

  it('is true from the start in edit_draft, regardless of bootstrap', async () => {
    installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForEditDraft({
        provider_draft_id: 'drf_existing',
        account_id: 'acc_1',
        to_recipients: ['someone@example.com'],
        cc_recipients: [],
        bcc_recipients: [],
        subject: 'Existing',
        body: 'body',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
        attachments: [],
      });
    });

    // accountSelectorLocked is `mode === 'edit_draft' || providerDraftId !== null`,
    // so even though we have not added any attachments, the dropdown is
    // already locked because the user is editing an existing draft.
    await waitFor(() => expect(result.current.mode).toBe('edit_draft'));
    expect(result.current.accountSelectorLocked).toBe(true);
  });
});

describe('useDraftComposer — bootstrap failure path', () => {
  it('records persistence.error and skips the upload when createDraft rejects', async () => {
    let createCallCount = 0;
    let addAttachmentCallCount = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () =>
        HttpResponse.json([gmailAccountFixture()]),
      ),
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts`, () => {
        createCallCount += 1;
        return HttpResponse.json(
          { error: { code: 'provider_unavailable', message: 'provider down' } },
          { status: 503 },
        );
      }),
      http.post(
        `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/attachments`,
        () => {
          addAttachmentCallCount += 1;
          return HttpResponse.json(draftAttachmentResponseFixture());
        },
      ),
    );

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    act(() => {
      result.current.addAttachmentFiles([makeFile('fail.pdf', 100)]);
    });

    await waitFor(() => {
      expect(createCallCount).toBe(1);
      expect(result.current.error?.code).toBe('provider_unavailable');
    });

    // The upload never fires because the bootstrap failed first.
    expect(addAttachmentCallCount).toBe(0);
    // No draft id was cached, so the lock state stays off.
    expect(result.current.accountSelectorLocked).toBe(false);
  });
});

describe('useDraftComposer — emptiness drives the dirty/close check', () => {
  it('treats a lone empty paragraph as not dirty (closeWithX closes immediately)', async () => {
    installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // ``seedForNew`` fixed the snapshot baseline to '' (no signature on this
    // fixture), and ``isDirty`` normalises both sides, so a residual ``<p></p>``
    // equals the baseline → not dirty → closeWithX closes without a dialog.
    act(() => {
      result.current.setBody('<p></p>');
    });
    act(() => {
      result.current.closeWithX();
    });
    expect(result.current.open).toBe(false);
    expect(result.current.closeDialogOpen).toBe(false);
  });

  it('treats real HTML content as dirty (closeWithX opens the discard dialog)', async () => {
    installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // A paragraph with text differs from the '' baseline → dirty, so the close
    // confirmation dialog must appear instead of silently discarding the draft.
    act(() => {
      result.current.setBody('<p>texto</p>');
    });
    act(() => {
      result.current.closeWithX();
    });
    expect(result.current.closeDialogOpen).toBe(true);
    expect(result.current.open).toBe(true);
  });
});

describe('useDraftComposer — body size gating', () => {
  it('flags bodyError and blocks the send-email action when the body exceeds the cap', async () => {
    installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // A valid recipient + small body: the send action is allowed and there
    // is no body error.
    act(() => {
      result.current.setTo('to@example.com');
      result.current.setBody('<p>hi</p>');
    });
    expect(result.current.bodyError).toBeNull();
    expect(result.current.canSendEmail).toBe(true);

    // Push the body past the 1,000,000-char cap: bodyError appears and the
    // send action is blocked (the client-side guard mirrors the backend's
    // Pydantic max_length).
    act(() => {
      result.current.setBody('x'.repeat(1_000_001));
    });
    expect(result.current.bodyError).not.toBeNull();
    expect(result.current.bodyError?.code).toBe('body_too_large');
    expect(result.current.canSendEmail).toBe(false);
  });

  it('blocks save-draft and send-draft on an existing draft when the body is too big', async () => {
    installBootstrapHandlers();

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    // edit_draft sets accountId + providerDraftId, enabling both draft
    // actions; the gating must also hold on these paths because
    // handleSendEmail reroutes through sendDraftNow once a draft exists.
    act(() => {
      result.current.openForEditDraft({
        provider_draft_id: 'drf_existing',
        account_id: 'acc_1',
        to_recipients: ['someone@example.com'],
        cc_recipients: [],
        bcc_recipients: [],
        subject: 'Existing',
        body: '<p>body</p>',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
        attachments: [],
      });
    });
    await waitFor(() => expect(result.current.mode).toBe('edit_draft'));

    expect(result.current.canSaveDraft).toBe(true);
    expect(result.current.canSendDraft).toBe(true);

    act(() => {
      result.current.setBody('x'.repeat(1_000_001));
    });
    expect(result.current.bodyError).not.toBeNull();
    expect(result.current.canSaveDraft).toBe(false);
    expect(result.current.canSendDraft).toBe(false);
  });
});

describe('useDraftComposer — account signature insertion', () => {
  const SIG = '<p>Jane Doe — Acme</p>';

  function signedAccount(accountId = 'acc_1') {
    return { ...gmailAccountFixture(accountId), signature_html: SIG };
  }

  it('inserts the account signature into the body in new_email', async () => {
    installBootstrapHandlers({ accounts: [signedAccount()] });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // Brand-new email: empty paragraph on top + the signature below it.
    await waitFor(() => expect(result.current.body).toBe(`<p></p>${SIG}`));
  });

  it('inserts the preset account signature in new_draft', async () => {
    installBootstrapHandlers({
      accounts: [gmailAccountFixture('acc_1'), signedAccount('acc_2')],
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewDraft({ accountId: 'acc_2' });
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_2'));
    await waitFor(() => expect(result.current.body).toBe(`<p></p>${SIG}`));
  });

  it('leaves the body empty when the chosen account has no signature', async () => {
    installBootstrapHandlers({ accounts: [gmailAccountFixture()] });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // signature_html: null ⇒ composeBodyWithSignature returns '' unchanged.
    expect(result.current.body).toBe('');
  });

  it('places the signature above the quote on reply, in body and persisted draft', async () => {
    const counters = { createDraft: 0 };
    const captured: { createDraftBodies: Record<string, unknown>[] } = { createDraftBodies: [] };
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([signedAccount()])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/emails/:pmid/reply-context`, () =>
        HttpResponse.json(replyContextFixture('reply')),
      ),
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts`, async ({ request }) => {
        counters.createDraft += 1;
        captured.createDraftBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({
          provider_draft_id: 'drf_sig_reply',
          account_id: 'acc_1',
          to_recipients: [],
          cc_recipients: [],
          bcc_recipients: [],
          subject: '',
          body: '',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
          attachments: [],
        });
      }),
    );

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    await act(async () => {
      await result.current.openForReply(emailMetadataFixture('pmid_1', 'acc_1'));
    });

    await waitFor(() => expect(result.current.mode).toBe('reply'));

    // The seeded form body carries the signature before the quoted blockquote.
    expect(result.current.body).toContain(SIG);
    expect(result.current.body.indexOf(SIG)).toBeLessThan(
      result.current.body.indexOf('<blockquote'),
    );
    // The SAME signed body is sent to the provider on createDraft (so the
    // provider draft and the local form stay consistent).
    const sentBody = captured.createDraftBodies[0].body as string;
    expect(sentBody).toContain(SIG);
    expect(sentBody.indexOf(SIG)).toBeLessThan(sentBody.indexOf('<blockquote'));
  });

  it('places the signature above the forwarded block on forward', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([signedAccount()])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/emails/:pmid/reply-context`, () =>
        HttpResponse.json(replyContextFixture('forward')),
      ),
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts`, () =>
        HttpResponse.json({
          provider_draft_id: 'drf_sig_fwd',
          account_id: 'acc_1',
          to_recipients: [],
          cc_recipients: [],
          bcc_recipients: [],
          subject: '',
          body: '',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
          attachments: [],
        }),
      ),
      http.post(
        `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/attachments/copy-from-email`,
        () => HttpResponse.json({ copied_count: 0, skipped: [], attachments: [] }),
      ),
    );

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    await act(async () => {
      await result.current.openForForward(emailMetadataFixture('pmid_1', 'acc_1'));
    });

    await waitFor(() => expect(result.current.mode).toBe('forward'));
    expect(result.current.body).toContain(SIG);
    expect(result.current.body.indexOf(SIG)).toBeLessThan(
      result.current.body.indexOf('Mensaje reenviado'),
    );
  });

  it('does NOT re-inject the signature when editing an existing draft', async () => {
    installBootstrapHandlers({ accounts: [signedAccount()] });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    // A draft saved earlier (its body already had whatever the user wanted).
    // Reopening it must show that body verbatim — no extra signature appended.
    act(() => {
      result.current.openForEditDraft({
        provider_draft_id: 'drf_existing',
        account_id: 'acc_1',
        to_recipients: ['someone@example.com'],
        cc_recipients: [],
        bcc_recipients: [],
        subject: 'Existing',
        body: '<p>existing body</p>',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
        attachments: [],
      });
    });

    await waitFor(() => expect(result.current.mode).toBe('edit_draft'));
    expect(result.current.body).toBe('<p>existing body</p>');
    expect(result.current.body).not.toContain(SIG);
  });

  it('swaps the signature when the account changes on a pristine new_email', async () => {
    installBootstrapHandlers({
      accounts: [signedAccount('acc_1'), gmailAccountFixture('acc_2')],
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.body).toBe(`<p></p>${SIG}`));

    // Switch to the account with no signature: the pristine body is replaced.
    act(() => {
      result.current.setAccountId('acc_2');
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_2'));
    expect(result.current.body).toBe('');
  });

  it('keeps the typed body when the account changes after the user edited it', async () => {
    installBootstrapHandlers({
      accounts: [gmailAccountFixture('acc_1'), signedAccount('acc_2')],
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    act(() => {
      result.current.openForNewEmail();
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_1'));

    // The user typed something → the compose is no longer pristine.
    act(() => {
      result.current.setBody('<p>my own text</p>');
    });
    // Switching account now must NOT clobber the typed body with acc_2's sig.
    act(() => {
      result.current.setAccountId('acc_2');
    });
    await waitFor(() => expect(result.current.accountId).toBe('acc_2'));
    expect(result.current.body).toBe('<p>my own text</p>');
  });
});

/**
 * Reply / Reply All / Forward — composer opens with prefilled state.
 *
 * The hook orchestrates three steps:
 *   1. ``GET /reply-context`` returns recipients / subject / quoted body /
 *      threading metadata.
 *   2. ``POST /drafts`` creates a real provider draft with the prefill
 *      (R-07 — the draft is created at click time, not on first edit).
 *   3. For ``forward`` only: ``POST .../copy-from-email`` materialises
 *      the inherited attachments (Gmail downloads + re-uploads, Outlook
 *      no-ops because ``createForward`` already inherited them).
 *
 * Each test installs handlers that capture the call sequence and asserts
 * the post-condition: mode flipped to the right kind, prefilled fields
 * visible in the form, ``providerDraftId`` cached for follow-up writes,
 * ``copy-from-email`` invoked only on Forward.
 */

function emailMetadataFixture(providerMessageId = 'pmid_1', accountId = 'acc_1') {
  // Minimal EmailMetadataOut shape — only the fields openForReply reads
  // (``provider_message_id`` and ``account_id``).
  return {
    provider_message_id: providerMessageId,
    account_id: accountId,
    mailbox_id: 'mb_1',
    thread_id: 't_1',
    from_email: 'ana@example.com',
    from_name: 'Ana Lopez',
    subject: 'Hello',
    received_at: '2026-05-23T14:32:00Z',
    is_read: true,
    box: 'ALL_MAIL' as const,
    is_favorite: false,
    has_attachments: false,
    thread_message_count: 1,
  };
}

function replyContextFixture(replyKind: 'reply' | 'reply_all' | 'forward') {
  // The server returns the full ReplyContextOut. Recipients are
  // populated for reply/reply_all and empty for forward (the user
  // fills them in §5.1).
  const base = {
    in_reply_to: '<orig@x>',
    references: '<orig@x>',
    thread_id: 't_1',
    reply_to_message_id: 'pmid_1',
    reply_kind: replyKind,
    original_from_email: 'ana@example.com',
    bcc_recipients: [] as string[],
  };
  if (replyKind === 'forward') {
    return {
      ...base,
      to_recipients: [] as string[],
      cc_recipients: [] as string[],
      subject: 'Fwd: Hello',
      // ``body`` is HTML now (rich-text composer): the forward block + the
      // original quoted inside a <blockquote>.
      body:
        '<p>---------- Mensaje reenviado ----------<br>De: Ana &lt;ana@example.com&gt;<br>Asunto: Hello</p>' +
        '<blockquote style="border-left:2px solid #ccc"><p>Hello</p></blockquote>',
    };
  }
  return {
    ...base,
    to_recipients: ['ana@example.com'],
    cc_recipients: replyKind === 'reply_all' ? ['carol@x.com'] : ([] as string[]),
    subject: 'Re: Hello',
    // ``body`` is HTML: attribution line + the original inside a <blockquote>.
    body:
      '<p>El 23 de mayo de 2026, Ana Lopez &lt;ana@example.com&gt; escribió:</p>' +
      '<blockquote style="border-left:2px solid #ccc"><p>Hello</p></blockquote>',
  };
}

function installReplyHandlers(
  opts: {
    replyKind?: 'reply' | 'reply_all' | 'forward';
    draftId?: string;
  } = {},
) {
  const replyKind = opts.replyKind ?? 'reply';
  const draftId = opts.draftId ?? 'drf_reply';
  const counters = {
    listAccounts: 0,
    replyContext: 0,
    createDraft: 0,
    copyFromEmail: 0,
  };
  const captured: {
    createDraftBodies: unknown[];
    replyContextActions: string[];
    copyFromEmailBodies: unknown[];
  } = {
    createDraftBodies: [],
    replyContextActions: [],
    copyFromEmailBodies: [],
  };

  server.use(
    http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => {
      counters.listAccounts += 1;
      return HttpResponse.json([gmailAccountFixture()]);
    }),
    http.get(
      `${API_BASE}/mailboxes/mb_1/accounts/:accountId/emails/:pmid/reply-context`,
      ({ request }) => {
        counters.replyContext += 1;
        const url = new URL(request.url);
        captured.replyContextActions.push(url.searchParams.get('action') ?? '');
        return HttpResponse.json(replyContextFixture(replyKind));
      },
    ),
    http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts`, async ({ request }) => {
      counters.createDraft += 1;
      captured.createDraftBodies.push(await request.json());
      return HttpResponse.json({
        provider_draft_id: draftId,
        account_id: 'acc_1',
        to_recipients: [],
        cc_recipients: [],
        bcc_recipients: [],
        subject: '',
        body: '',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
        attachments: [],
      });
    }),
    http.post(
      `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/attachments/copy-from-email`,
      async ({ request }) => {
        counters.copyFromEmail += 1;
        captured.copyFromEmailBodies.push(await request.json());
        return HttpResponse.json({
          copied_count: 1,
          skipped: [],
          attachments: [
            {
              draft_attachment_id: '11111111-1111-4000-a000-aaaaaaaaaaaa',
              filename: 'inherited.pdf',
              mime_type: 'application/pdf',
              size: 100,
              position: 0,
              provider_attachment_id: null,
            },
          ],
        });
      },
    ),
  );

  return { counters, captured };
}

describe('useDraftComposer — openForReply', () => {
  it('fetches reply context, creates the draft, and switches mode to reply', async () => {
    const { counters, captured } = installReplyHandlers({
      replyKind: 'reply',
      draftId: 'drf_reply_1',
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    await act(async () => {
      await result.current.openForReply(emailMetadataFixture('pmid_1', 'acc_1'));
    });

    // The composer is now in reply mode with the prefilled form.
    await waitFor(() => {
      expect(result.current.mode).toBe('reply');
      expect(result.current.subject).toBe('Re: Hello');
      // To-recipients prefilled from the reply context.
      expect(result.current.to).toBe('ana@example.com');
    });

    // The seeded body is the HTML quote from reply-context. This hook is a
    // ``renderHook`` test that does NOT mount RichTextEditor (the editor lives
    // inside ComposeOverlay), so ``result.current.body`` is exactly the
    // ``context.body`` ``seedForReply`` stored via ``setBody`` — verbatim,
    // without passing through ProseMirror.
    expect(result.current.body).toContain('<blockquote');

    // One call to each of: reply-context + create-draft.
    expect(counters.replyContext).toBe(1);
    expect(counters.createDraft).toBe(1);
    // Reply MUST NOT trigger copy-from-email (that's forward-only).
    expect(counters.copyFromEmail).toBe(0);
    // The action query param was forwarded as ``reply``.
    expect(captured.replyContextActions[0]).toBe('reply');

    // The provider_draft_id flips the lock + transitions the composer
    // into "edit-an-existing-draft"-like behaviour.
    expect(result.current.accountSelectorLocked).toBe(true);
  });
});

describe('useDraftComposer — openForReplyAll', () => {
  it('passes action=reply_all to /reply-context and seeds the CC field', async () => {
    const { counters, captured } = installReplyHandlers({
      replyKind: 'reply_all',
      draftId: 'drf_replyall_1',
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    await act(async () => {
      await result.current.openForReplyAll(emailMetadataFixture('pmid_1', 'acc_1'));
    });

    await waitFor(() => expect(result.current.mode).toBe('reply_all'));
    expect(captured.replyContextActions[0]).toBe('reply_all');
    // The reply context fixture seeds a CC for reply_all.
    expect(result.current.cc).toBe('carol@x.com');
    expect(counters.copyFromEmail).toBe(0);
  });
});

describe('useDraftComposer — openForForward', () => {
  it('creates the draft and then invokes copy-from-email exactly once', async () => {
    const { counters, captured } = installReplyHandlers({
      replyKind: 'forward',
      draftId: 'drf_fwd_1',
    });

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    await act(async () => {
      await result.current.openForForward(emailMetadataFixture('pmid_1', 'acc_1'));
    });

    await waitFor(() => expect(result.current.mode).toBe('forward'));
    // Forward subject is prefixed.
    expect(result.current.subject).toBe('Fwd: Hello');
    // Forward pre-fills NO recipients (the user adds them).
    expect(result.current.to).toBe('');

    expect(counters.replyContext).toBe(1);
    expect(counters.createDraft).toBe(1);
    // copy-from-email IS invoked for Forward.
    expect(counters.copyFromEmail).toBe(1);

    // The copy-from-email body carries the source pointers (the
    // original message + its account).
    const copyBody = captured.copyFromEmailBodies[0] as Record<string, unknown>;
    expect(copyBody.source_account_id).toBe('acc_1');
    expect(copyBody.source_provider_message_id).toBe('pmid_1');
  });

  it('surfaces persistence.error when copy-from-email fails (composer still opens)', async () => {
    // Soft-fail contract: a failed copy-from-email must NOT block the
    // composer — it just shows the error.
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () =>
        HttpResponse.json([gmailAccountFixture()]),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/emails/:pmid/reply-context`, () =>
        HttpResponse.json(replyContextFixture('forward')),
      ),
      http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts`, () =>
        HttpResponse.json({
          provider_draft_id: 'drf_fwd_fail',
          account_id: 'acc_1',
          to_recipients: [],
          cc_recipients: [],
          bcc_recipients: [],
          subject: '',
          body: '',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
          attachments: [],
        }),
      ),
      http.post(
        `${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/attachments/copy-from-email`,
        () =>
          HttpResponse.json(
            { error: { code: 'attachment_provider_unavailable', message: 'down' } },
            { status: 503 },
          ),
      ),
    );

    const { result } = renderHook(() => useDraftComposer('mb_1'));

    await act(async () => {
      await result.current.openForForward(emailMetadataFixture('pmid_1', 'acc_1'));
    });

    // The composer opened anyway (mode flipped to forward) and the error
    // is surfaced as a UiError.
    await waitFor(() => {
      expect(result.current.mode).toBe('forward');
      expect(result.current.error?.code).toBe('attachment_provider_unavailable');
    });
  });
});
