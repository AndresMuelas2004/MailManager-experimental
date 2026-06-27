import { useCallback, useEffect, useRef, useState } from 'react';

import { listAccounts } from '../../../api/endpoints/accounts';
import { getReplyContext } from '../../../api/endpoints/emails';
import { copyAttachmentsFromEmail, createDraft } from '../../../api/endpoints/drafts';
import { toUiError } from '../../../api/client/errors';
import { composeBodyWithSignature } from '../../../lib/richText';
import { useTranslation } from '../../../lib/i18n';
import type {
  AccountOut,
  DraftOut,
  EmailMetadataOut,
  FailedAttachmentDetail,
  ReplyKindDto,
} from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';
import type { ComposerMode, ReplyKind } from '../../../lib/types';
import useComposerForm, { type ReplyMetadata } from './useComposerForm';
import useComposerAttachments, {
  type AttachmentChip,
  type AttachmentTarget,
} from './useComposerAttachments';
import useDraftPersistence from './useDraftPersistence';

type OpenNewDraftArgs = {
  accountId?: string;
};

const REPLY_KIND_TO_MODE: Record<ReplyKind, ComposerMode> = {
  reply: 'reply',
  reply_all: 'reply_all',
  forward: 'forward',
};

// Client-side body size guard (mirrors the backend's Pydantic ``max_length``).
// This length-check is the REAL size defence in the client: the ``.max()`` on
// the Zod *request* schemas never runs at runtime (``request<T>()`` only
// validates responses), so the server-side 422 would otherwise be the first
// signal — and it does not arrive as a readable message (see ``useDraftPersistence``).
const BODY_MAX_CHARS = 1_000_000;

type UseDraftComposerReturn = {
  open: boolean;
  mode: ComposerMode | null;
  accounts: AccountOut[];
  accountId: string;
  setAccountId: (id: string) => void;
  to: string;
  setTo: (v: string) => void;
  cc: string;
  setCc: (v: string) => void;
  bcc: string;
  setBcc: (v: string) => void;
  subject: string;
  setSubject: (v: string) => void;
  body: string;
  setBody: (v: string) => void;
  sending: boolean;
  saving: boolean;
  error: UiError | null;
  recipientError: UiError | null;
  bodyError: UiError | null;
  canSendEmail: boolean;
  canSaveDraft: boolean;
  canSendDraft: boolean;
  openForNewEmail: () => void;
  openForNewDraft: (args?: OpenNewDraftArgs) => void;
  openForEditDraft: (draft: DraftOut) => void;
  openForReply: (email: EmailMetadataOut) => Promise<void>;
  openForReplyAll: (email: EmailMetadataOut) => Promise<void>;
  openForForward: (email: EmailMetadataOut) => Promise<void>;
  replyContextLoading: boolean;
  closeWithX: () => void;
  confirmCloseSave: () => Promise<void>;
  confirmCloseDiscard: () => Promise<void>;
  cancelClose: () => void;
  handleSendEmail: () => Promise<void>;
  handleSaveDraft: () => Promise<void>;
  handleSendDraft: () => Promise<void>;
  setRefreshCallback: (fn: (() => void | Promise<void>) | null) => void;
  // Attachments
  attachmentsEnabled: boolean;
  accountSelectorLocked: boolean;
  attachmentChips: AttachmentChip[];
  attachmentTotalSize: number;
  addAttachmentFiles: (files: File[]) => void;
  removeAttachmentChip: (chipId: string) => void;
  // Close-confirmation dialog (D-28)
  closeDialogOpen: boolean;
  // Send-failed dialog (D-27)
  sendFailedOpen: boolean;
  failedAttachments: FailedAttachmentDetail[];
  retrySend: () => Promise<void>;
  removeFailedAndRetrySend: () => Promise<void>;
  closeSendFailedDialog: () => void;
};

export default function useDraftComposer(mailboxId: string | null): UseDraftComposerReturn {
  const { t } = useTranslation();
  const [mode, setMode] = useState<ComposerMode | null>(null);
  const [accounts, setAccounts] = useState<AccountOut[]>([]);
  const [providerDraftId, setProviderDraftIdState] = useState<string | null>(null);
  const [refreshCallback, setRefreshCallbackState] = useState<(() => void | Promise<void>) | null>(
    null,
  );
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [sendFailedOpen, setSendFailedOpen] = useState(false);
  const [failedAttachments, setFailedAttachments] = useState<FailedAttachmentDetail[]>([]);
  // Override for cross-mailbox Reply / Reply All / Forward: when the
  // composer opens on an email whose ``mailbox_id`` differs from the
  // route's ``mailboxId`` (virtual mailbox aggregating accounts from
  // several real mailboxes), every backend call from the composer
  // must target the email's real mailbox, not the URL's. Set in
  // ``openForReplyKind`` before the first fetch and cleared by
  // ``resetAll``.
  const [composerMailboxOverride, setComposerMailboxOverride] = useState<string | null>(null);
  const effectiveMailboxId = composerMailboxOverride ?? mailboxId;
  const form = useComposerForm();
  const attachments = useComposerAttachments();
  const persistence = useDraftPersistence();
  const creatingDraftRef = useRef<Promise<string> | null>(null);
  const pendingFilesRef = useRef<File[]>([]);
  // Tracks which mailbox the cached ``accounts`` state belongs to so a
  // cross-mailbox Reply (override active) cannot leave the URL mailbox
  // path reading the wrong cached list on a subsequent open.
  const accountsMailboxIdRef = useRef<string | null>(null);

  const buildAttachmentTarget = useCallback((): AttachmentTarget | null => {
    if (!effectiveMailboxId || !form.accountId || !providerDraftId) return null;
    return { mailboxId: effectiveMailboxId, accountId: form.accountId, providerDraftId };
  }, [effectiveMailboxId, form.accountId, providerDraftId]);

  const setRefreshCallback = useCallback((fn: (() => void | Promise<void>) | null) => {
    setRefreshCallbackState(() => fn);
  }, []);

  const triggerRefresh = useCallback(async () => {
    if (refreshCallback) {
      try {
        await refreshCallback();
      } catch {
        // refresh failures are non-fatal for the composer flow
      }
    }
  }, [refreshCallback]);

  const resetAll = useCallback(() => {
    form.reset();
    attachments.reset();
    persistence.setError(null);
    persistence.setProviderDraftId(null);
    setProviderDraftIdState(null);
    setCloseDialogOpen(false);
    setSendFailedOpen(false);
    setFailedAttachments([]);
    setComposerMailboxOverride(null);
    creatingDraftRef.current = null;
    pendingFilesRef.current = [];
  }, [form, attachments, persistence]);

  const loadAccountsIfNeeded = useCallback(async () => {
    if (!mailboxId) return accounts;
    if (accounts.length > 0 && accountsMailboxIdRef.current === mailboxId) return accounts;
    try {
      const accs = await listAccounts(mailboxId);
      setAccounts(accs);
      accountsMailboxIdRef.current = mailboxId;
      return accs;
    } catch {
      return [] as AccountOut[];
    }
  }, [accounts, mailboxId]);

  const openForNewEmail = useCallback(() => {
    if (!mailboxId) return;
    resetAll();
    setMode('new_email');
    loadAccountsIfNeeded().then((accs) => {
      if (accs.length > 0 && !form.accountId) {
        const acc = accs[0];
        form.seedForNew({
          accountId: acc.account_id,
          body: composeBodyWithSignature('', acc.signature_html),
        });
      }
    });
  }, [form, loadAccountsIfNeeded, mailboxId, resetAll]);

  const openForNewDraft = useCallback(
    (args?: OpenNewDraftArgs) => {
      if (!mailboxId) return;
      resetAll();
      setMode('new_draft');
      const preset = args?.accountId;
      loadAccountsIfNeeded().then((accs) => {
        const chosen =
          preset && accs.some((a) => a.account_id === preset)
            ? accs.find((a) => a.account_id === preset)!
            : accs[0];
        if (chosen) {
          form.seedForNew({
            accountId: chosen.account_id,
            body: composeBodyWithSignature('', chosen.signature_html),
          });
        }
      });
    },
    [form, loadAccountsIfNeeded, mailboxId, resetAll],
  );

  const openForEditDraft = useCallback(
    (draft: DraftOut) => {
      if (!mailboxId) return;
      resetAll();
      setMode('edit_draft');
      form.seedFromDraft(draft);
      attachments.seedFromDraft(draft.attachments);
      setProviderDraftIdState(draft.provider_draft_id);
      persistence.setProviderDraftId(draft.provider_draft_id);
      loadAccountsIfNeeded();
    },
    [attachments, form, loadAccountsIfNeeded, mailboxId, persistence, resetAll],
  );

  const [replyContextLoading, setReplyContextLoading] = useState(false);

  const openForReplyKind = useCallback(
    async (email: EmailMetadataOut, action: ReplyKind) => {
      if (!mailboxId) return;
      resetAll();
      setReplyContextLoading(true);
      persistence.setError(null);
      // The email's real mailbox can diverge from the URL's ``mailboxId``
      // when the listing is a virtual mailbox aggregating accounts from
      // several real mailboxes. Every backend call below — and every
      // subsequent composer op (send / save / delete / attach) — must
      // target the email's real mailbox via ``effectiveMailboxId``.
      const targetMailboxId = email.mailbox_id;
      setComposerMailboxOverride(targetMailboxId);
      try {
        let accountsList: AccountOut[];
        if (targetMailboxId === mailboxId) {
          accountsList = await loadAccountsIfNeeded();
        } else {
          accountsList = await listAccounts(targetMailboxId).catch(() => [] as AccountOut[]);
          // Replace the cached account list so the ComposeOverlay
          // selector renders the email's account label (the URL
          // mailbox's accounts would not contain it). Track which
          // mailbox the cache now belongs to so the next open against
          // ``mailboxId`` refetches instead of reusing stale data.
          setAccounts(accountsList);
          accountsMailboxIdRef.current = targetMailboxId;
        }
        const accountId = email.account_id;
        if (!accountsList.some((a) => a.account_id === accountId)) {
          persistence.setError({
            message: t('composerErrors.accountNotFound'),
            code: 'account_not_found',
          });
          return;
        }

        // Account signature of the sender, inserted ABOVE the quoted reply /
        // forward body (empty line → signature → attribution → blockquote).
        // Seeded into BOTH the provider draft and the local form (and the
        // snapshot) so the threading and the signature stay consistent.
        const signatureHtml =
          accountsList.find((a) => a.account_id === accountId)?.signature_html ?? '';

        // 1. Fetch reply context (recipients, subject, quoted body,
        //    threading metadata). ~200ms typical — spinner UI is on.
        const context = await getReplyContext(
          targetMailboxId,
          accountId,
          email.provider_message_id,
          action as ReplyKindDto,
        );

        const seededBody = composeBodyWithSignature(context.body, signatureHtml);

        const replyMetadata: ReplyMetadata = {
          replyKind: context.reply_kind,
          replyToMessageId: context.reply_to_message_id,
          replyToAccountId: accountId,
          threadId: context.thread_id,
          inReplyTo: context.in_reply_to,
          referencesHeader: context.references,
        };

        // 2. Create the provider draft right away (R-07). The composer
        //    needs a ``provider_draft_id`` before it can accept
        //    attachments (D-07), and creating the draft pre-emptively
        //    matches Gmail/Outlook web's behaviour.
        const created = await createDraft(targetMailboxId, accountId, {
          to_recipients: context.to_recipients,
          cc_recipients: context.cc_recipients,
          bcc_recipients: context.bcc_recipients,
          subject: context.subject,
          body: seededBody,
          reply_kind: context.reply_kind,
          reply_to_message_id: context.reply_to_message_id,
          reply_to_account_id: accountId,
          thread_id: context.thread_id,
          in_reply_to: context.in_reply_to,
          references_header: context.references,
        });

        // 3. Seed the form state + register the provider_draft_id so
        //    the composer treats this as an existing draft (locked
        //    account selector, send-draft button).
        form.seedForReply({
          accountId,
          to: context.to_recipients,
          cc: context.cc_recipients,
          subject: context.subject,
          body: seededBody,
          replyMetadata,
        });
        attachments.seedFromDraft(created.attachments);
        setProviderDraftIdState(created.provider_draft_id);
        persistence.setProviderDraftId(created.provider_draft_id);
        setMode(REPLY_KIND_TO_MODE[action]);

        // 4. Forward: copy attachments from the original message.
        //    The endpoint is uniform across providers (Outlook no-op
        //    because createForward already inherited them at step 2).
        if (action === 'forward') {
          try {
            const copyResult = await copyAttachmentsFromEmail(
              targetMailboxId,
              accountId,
              created.provider_draft_id,
              {
                accountId,
                providerMessageId: email.provider_message_id,
              },
            );
            attachments.seedFromDraft(copyResult.attachments);
          } catch (err) {
            // Soft-fail: the forward composer still opens; the user
            // can re-add attachments manually if needed.
            persistence.setError(toUiError(err));
          }
        }
      } catch (err) {
        persistence.setError(toUiError(err));
      } finally {
        setReplyContextLoading(false);
      }
    },
    [attachments, form, loadAccountsIfNeeded, mailboxId, persistence, resetAll, t],
  );

  const openForReply = useCallback(
    (email: EmailMetadataOut) => openForReplyKind(email, 'reply'),
    [openForReplyKind],
  );
  const openForReplyAll = useCallback(
    (email: EmailMetadataOut) => openForReplyKind(email, 'reply_all'),
    [openForReplyKind],
  );
  const openForForward = useCallback(
    (email: EmailMetadataOut) => openForReplyKind(email, 'forward'),
    [openForReplyKind],
  );

  // Account selector change. In a pristine new_email / new_draft (no provider
  // draft yet, nothing typed since the seed) switching account replaces the
  // auto-signature with the new account's one. Once the user has edited the
  // body — or in any mode with a provider draft / locked selector — it falls
  // back to a plain ``setAccountId`` so we never clobber the user's content.
  const handleAccountChange = useCallback(
    (id: string) => {
      const canSwap =
        (mode === 'new_email' || mode === 'new_draft') &&
        providerDraftId === null &&
        !form.isDirty();
      if (canSwap) {
        const nextSig = accounts.find((a) => a.account_id === id)?.signature_html ?? '';
        form.seedForNew({ accountId: id, body: composeBodyWithSignature('', nextSig) });
      } else {
        form.setAccountId(id);
      }
    },
    [accounts, form, mode, providerDraftId],
  );

  const close = useCallback(() => {
    setMode(null);
    resetAll();
  }, [resetAll]);

  const handleSendEmail = useCallback(async () => {
    if (!effectiveMailboxId || !form.accountId) return;
    const recipients = form.parseRecipients(form.to);
    if (recipients.length === 0) return;
    let ok: boolean;
    if (providerDraftId !== null) {
      ok = await persistence.sendDraftNow(
        effectiveMailboxId,
        form.accountId,
        providerDraftId,
        form.buildDraftPayload(),
      );
    } else {
      ok = await persistence.sendEmailNow(
        effectiveMailboxId,
        form.accountId,
        recipients,
        form.subject,
        form.body,
      );
    }
    if (ok) {
      close();
      await triggerRefresh();
    }
  }, [close, effectiveMailboxId, form, persistence, providerDraftId, triggerRefresh]);

  const handleSaveDraft = useCallback(async () => {
    if (!effectiveMailboxId || !form.accountId) return;
    const ok = await persistence.saveDraftNow(
      effectiveMailboxId,
      form.accountId,
      form.buildDraftPayload(),
    );
    if (ok) {
      close();
      await triggerRefresh();
    }
  }, [close, effectiveMailboxId, form, persistence, triggerRefresh]);

  const sendDraftCore = useCallback(async (): Promise<boolean> => {
    if (!effectiveMailboxId || !form.accountId || !providerDraftId) return false;
    const payloadIfDirty = form.isDirty() ? form.buildDraftPayload() : null;
    try {
      const ok = await persistence.sendDraftNow(
        effectiveMailboxId,
        form.accountId,
        providerDraftId,
        payloadIfDirty,
      );
      return ok;
    } catch (error) {
      // The persistence helper currently swallows errors and reports
      // ``ok === false`` plus ``persistence.error``. If a future
      // refactor surfaces ``attachment_send_failed`` as a thrown
      // ApiError with ``detail.failed_attachments``, this catch block
      // routes it into the dialog flow (D-27).
      const apiError = error as { detail?: { failed_attachments?: FailedAttachmentDetail[] } };
      const list = apiError?.detail?.failed_attachments;
      if (Array.isArray(list)) {
        setFailedAttachments(list);
        setSendFailedOpen(true);
      }
      return false;
    }
  }, [effectiveMailboxId, form, persistence, providerDraftId]);

  const handleSendDraft = useCallback(async () => {
    const ok = await sendDraftCore();
    if (ok) {
      close();
      await triggerRefresh();
    } else if (
      persistence.error &&
      persistence.error.code === 'attachment_send_failed' &&
      !sendFailedOpen
    ) {
      // Persistence layer surfaced the failure but did not open the
      // dialog (no detail propagation through the legacy path); show
      // a simple error in the composer body. The dialog requires a
      // typed payload that the legacy ``sendDraftNow`` does not yet
      // expose; tracked as future work (D-27 polish).
      setFailedAttachments([]);
      setSendFailedOpen(true);
    }
  }, [close, persistence.error, sendDraftCore, sendFailedOpen, triggerRefresh]);

  const retrySend = useCallback(async () => {
    const ok = await sendDraftCore();
    if (ok) {
      setSendFailedOpen(false);
      setFailedAttachments([]);
      close();
      await triggerRefresh();
    }
  }, [close, sendDraftCore, triggerRefresh]);

  const removeFailedAndRetrySend = useCallback(async () => {
    const target = buildAttachmentTarget();
    if (!target) return;
    // Best-effort: drop the failed chips from the local list AND from
    // the backend, then retry the send.
    for (const failed of failedAttachments) {
      try {
        await attachments.removeChip(failed.draft_attachment_id, target);
      } catch {
        // continue removing the rest
      }
    }
    await retrySend();
  }, [attachments, buildAttachmentTarget, failedAttachments, retrySend]);

  const closeSendFailedDialog = useCallback(() => {
    setSendFailedOpen(false);
    setFailedAttachments([]);
  }, []);

  const closeWithX = useCallback(() => {
    const currentMode = mode;

    // D-28: pending changes (form fields or unsynced attachments)
    // must trigger the explicit save / discard / cancel dialog.
    // Reply / Reply All / Forward modes always have a provider_draft_id
    // (R-07 created it eagerly), so the dialog runs the same flow as
    // edit_draft — the user picks Save (persist) or Discard (delete
    // the provider draft).
    // Every content mode now uses ``isDirty()`` against the snapshot fixed at
    // seed time: ``seedForNew`` fixes a baseline for new_email / new_draft, so
    // an auto-inserted signature the user has NOT touched reads as not dirty
    // and closing does not pop the save dialog.
    const dirtyForm =
      currentMode === 'new_email' ||
      currentMode === 'new_draft' ||
      currentMode === 'edit_draft' ||
      currentMode === 'reply' ||
      currentMode === 'reply_all' ||
      currentMode === 'forward'
        ? form.isDirty()
        : false;
    // Attachments are dirty only when the live set diverges from the baseline
    // seeded from the saved draft (``attachments.seedFromDraft``). Reopening a
    // draft with its saved attachments untouched is therefore NOT dirty, while
    // an added or removed attachment still is.
    const dirtyAttachments = attachments.isDirty();

    if (dirtyForm || dirtyAttachments) {
      setCloseDialogOpen(true);
      return;
    }
    close();
  }, [attachments, close, form, mode]);

  const confirmCloseSave = useCallback(async () => {
    setCloseDialogOpen(false);
    if (!effectiveMailboxId || !form.accountId) {
      close();
      return;
    }
    try {
      await persistence.persistDraft(effectiveMailboxId, form.accountId, form.buildDraftPayload());
      await triggerRefresh();
    } catch {
      // Surface error in the composer body via persistence.error; do
      // not close so the user can react.
      return;
    }
    close();
  }, [close, effectiveMailboxId, form, persistence, triggerRefresh]);

  const confirmCloseDiscard = useCallback(async () => {
    setCloseDialogOpen(false);
    // For an existing draft, the safest discard is a real DELETE on
    // the provider — that wipes ``draft_attachments`` via CASCADE too.
    if (effectiveMailboxId && form.accountId && providerDraftId) {
      try {
        const { deleteDraft } = await import('../../../api/endpoints/drafts');
        await deleteDraft(effectiveMailboxId, form.accountId, providerDraftId);
      } catch {
        // best-effort; the local draft will linger until next sync
      }
    }
    await triggerRefresh();
    close();
  }, [close, effectiveMailboxId, form.accountId, providerDraftId, triggerRefresh]);

  const cancelClose = useCallback(() => {
    setCloseDialogOpen(false);
  }, []);

  // Reply / Reply All / Forward share the same UI surface as edit_draft
  // (Save + Send buttons). The mode set drives the title in the
  // Overlay and the way the composer behaves on close (D-28 dialog).
  const isDraftMode =
    mode === 'new_draft' ||
    mode === 'edit_draft' ||
    mode === 'reply' ||
    mode === 'reply_all' ||
    mode === 'forward';

  const isSendDraftMode =
    mode === 'edit_draft' || mode === 'reply' || mode === 'reply_all' || mode === 'forward';

  // Client-side recipient validation. ``parseRecipients`` strips empty
  // tokens but does NOT enforce email shape; without this guard the
  // Send button stays enabled with a malformed address (e.g. ``foo``),
  // the request hits the provider, and the provider's 400 surfaces as
  // a 502 with a technical message in the composer. Blocking the send
  // here keeps the failure local and lets the overlay render a neutral
  // "dirección de correo no válida" hint.
  const recipientsInvalid =
    form.hasInvalidRecipients(form.to) ||
    form.hasInvalidRecipients(form.cc) ||
    form.hasInvalidRecipients(form.bcc);

  const recipientError: UiError | null = recipientsInvalid
    ? { message: t('composerErrors.invalidRecipient'), code: 'invalid_recipient' }
    : null;

  // Oversized HTML body. ``form.body`` is the live editor HTML (already
  // normalised on every ``onChange``). Gating all three actions on
  // ``!bodyError`` — not just ``canSendEmail`` — is required because
  // ``handleSendEmail`` reroutes through ``sendDraftNow`` once a silent
  // draft exists, so the size block must hold on the draft paths too.
  const bodyError: UiError | null =
    form.body.length > BODY_MAX_CHARS
      ? { message: t('composerErrors.bodyTooLarge'), code: 'body_too_large' }
      : null;

  // The "Revisa que el asunto y el mensaje no estén vacíos." warning is raised
  // by the last send/save attempt's 422 and lives in ``persistence.error``
  // (state), unlike ``recipientError`` / ``bodyError`` which are re-derived on
  // every render. Resolve it reactively here — reusing the same per-render
  // derivation as the recipient warning — the moment subject AND body are both
  // non-empty again, so the stale notice clears live as the user fills the
  // fields instead of lingering until the composer closes. ``length > 0``
  // mirrors the backend's ``min_length=1`` on ``subject`` / ``body`` (an empty
  // editor serialises to ``""``); other error codes pass through untouched.
  const displayError: UiError | null =
    persistence.error?.code === 'validation_error' &&
    form.subject.length > 0 &&
    form.body.length > 0
      ? null
      : persistence.error;

  const canSendEmail =
    mode === 'new_email' &&
    form.accountId.length > 0 &&
    form.parseRecipients(form.to).length > 0 &&
    !recipientsInvalid &&
    !bodyError &&
    !persistence.sending;

  const canSaveDraft =
    isDraftMode && form.accountId.length > 0 && !bodyError && !persistence.saving;

  const canSendDraft =
    isSendDraftMode &&
    form.accountId.length > 0 &&
    providerDraftId !== null &&
    form.parseRecipients(form.to).length > 0 &&
    !recipientsInvalid &&
    !bodyError &&
    !persistence.sending;

  const attachmentsEnabled = mode !== null && form.accountId !== '';
  const accountSelectorLocked = mode === 'edit_draft' || providerDraftId !== null;

  const ensureBootstrappedTarget = useCallback(async (): Promise<AttachmentTarget | null> => {
    if (!effectiveMailboxId || !form.accountId) return null;
    if (providerDraftId !== null) {
      return { mailboxId: effectiveMailboxId, accountId: form.accountId, providerDraftId };
    }
    if (creatingDraftRef.current) {
      const id = await creatingDraftRef.current;
      return { mailboxId: effectiveMailboxId, accountId: form.accountId, providerDraftId: id };
    }
    const promise = persistence
      .ensureProviderDraftId(effectiveMailboxId, form.accountId, form.buildDraftPayload())
      .then((id) => {
        setProviderDraftIdState(id);
        return id;
      });
    creatingDraftRef.current = promise;
    try {
      const id = await promise;
      return { mailboxId: effectiveMailboxId, accountId: form.accountId, providerDraftId: id };
    } finally {
      creatingDraftRef.current = null;
    }
  }, [effectiveMailboxId, form, persistence, providerDraftId]);

  const addAttachmentFiles = useCallback(
    (files: File[]) => {
      if (!effectiveMailboxId) return;
      if (!form.accountId) {
        pendingFilesRef.current = [...pendingFilesRef.current, ...files];
        return;
      }
      // Hand the bootstrap to ``addFiles`` as a deferred resolver: client-side
      // validation runs first and the silent provider-draft bootstrap fires
      // only once a file passes, so a fully-rejected drop (e.g. a blocked .exe)
      // never creates a phantom draft nor locks the account selector.
      attachments.addFiles(files, ensureBootstrappedTarget);
    },
    [attachments, effectiveMailboxId, ensureBootstrappedTarget, form.accountId],
  );

  const removeAttachmentChip = useCallback(
    (chipId: string) => {
      const target = buildAttachmentTarget();
      if (!target) return;
      attachments.removeChip(chipId, target).catch(() => {});
    },
    [attachments, buildAttachmentTarget],
  );

  useEffect(() => {
    if (!effectiveMailboxId || !form.accountId || pendingFilesRef.current.length === 0) return;
    const pending = pendingFilesRef.current;
    pendingFilesRef.current = [];
    addAttachmentFiles(pending);
  }, [effectiveMailboxId, form.accountId, addAttachmentFiles]);

  useEffect(() => {
    if (mode === null) {
      pendingFilesRef.current = [];
      creatingDraftRef.current = null;
    }
  }, [mode]);

  return {
    open: mode !== null,
    mode,
    accounts,
    accountId: form.accountId,
    setAccountId: handleAccountChange,
    to: form.to,
    setTo: form.setTo,
    cc: form.cc,
    setCc: form.setCc,
    bcc: form.bcc,
    setBcc: form.setBcc,
    subject: form.subject,
    setSubject: form.setSubject,
    body: form.body,
    setBody: form.setBody,
    sending: persistence.sending,
    saving: persistence.saving,
    error: displayError,
    recipientError,
    bodyError,
    canSendEmail,
    canSaveDraft,
    canSendDraft,
    openForNewEmail,
    openForNewDraft,
    openForEditDraft,
    openForReply,
    openForReplyAll,
    openForForward,
    replyContextLoading,
    closeWithX,
    confirmCloseSave,
    confirmCloseDiscard,
    cancelClose,
    handleSendEmail,
    handleSaveDraft,
    handleSendDraft,
    setRefreshCallback,
    attachmentsEnabled,
    accountSelectorLocked,
    attachmentChips: attachments.chips,
    attachmentTotalSize: attachments.totalSize,
    addAttachmentFiles,
    removeAttachmentChip,
    closeDialogOpen,
    sendFailedOpen,
    failedAttachments,
    retrySend,
    removeFailedAndRetrySend,
    closeSendFailedDialog,
  };
}
