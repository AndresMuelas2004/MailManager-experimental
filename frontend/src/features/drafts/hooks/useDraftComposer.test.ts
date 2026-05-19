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

import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '../../../test/msw/server';
import useDraftComposer from './useDraftComposer';

const API_BASE = 'http://localhost:8000';

function gmailAccountFixture(accountId = 'acc_1') {
  return {
    account_id: accountId,
    mailbox_id: 'mb_1',
    provider: 'gmail',
    display_label: 'Gmail',
    config: {},
    email_address: 'tester@example.com',
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
function installBootstrapHandlers(opts: {
  accounts?: ReturnType<typeof gmailAccountFixture>[];
  draftId?: string;
  delayedAccounts?: Promise<unknown> | null;
  attachmentResponseFn?: (n: number) => ReturnType<typeof draftAttachmentResponseFixture>;
} = {}) {
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
    http.post(`${API_BASE}/mailboxes/mb_1/accounts/:accountId/drafts/:draftId/send`, ({ request }) => {
      counters.sendDraft += 1;
      captured.sendDraftPaths.push(new URL(request.url).pathname);
      return HttpResponse.json({
        provider_message_id: 'msg_sent',
        provider: 'gmail',
        status: 'sent',
      });
    }),
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
    expect(captured.sendDraftPaths[0]).toBe(
      '/mailboxes/mb_1/accounts/acc_1/drafts/drf_redir/send',
    );
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
