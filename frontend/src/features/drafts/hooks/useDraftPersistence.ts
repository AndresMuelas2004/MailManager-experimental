import { useCallback, useRef, useState } from 'react';

import { createDraft, updateDraft, sendDraft } from '../../../api/endpoints/drafts';
import { sendEmail } from '../../../api/endpoints/emails';
import { isApiError, toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { DraftPayload } from './useComposerForm';

// A send/draft 422 is a FastAPI validation error: its body is the object
// ``{ detail: [...] }`` (not the ``{ error: { code, message } }`` envelope),
// so ``toApiError`` falls back to ``http_error`` / 422 and stashes that payload
// on ``ApiError.detail`` — otherwise ``toUiError`` would surface an opaque
// English string. The two reachable validation failures need OPPOSITE messages:
// a body OVER the size cap (``string_too_long``) reads "demasiado grande" (only
// reachable by bypassing the client-side ``bodyError`` gate), while a missing
// subject/body/recipients (``string_too_short`` / min length) must read as a
// generic validation notice — never the size message, which is the opposite
// problem. ``status`` is read off the raw ``ApiError`` because ``toUiError``
// drops it.
function is422BodyTooLarge(detail: Record<string, unknown> | undefined): boolean {
  const items = detail?.detail;
  return (
    Array.isArray(items) &&
    items.some(
      (item) =>
        typeof item === 'object' &&
        item !== null &&
        (item as { type?: unknown }).type === 'string_too_long',
    )
  );
}

function toComposerError(err: unknown): UiError {
  if (isApiError(err) && err.code === 'http_error' && err.status === 422) {
    if (is422BodyTooLarge(err.detail)) {
      return {
        message: 'El mensaje es demasiado grande. Reduce su tamaño.',
        code: 'body_too_large',
      };
    }
    return {
      message: 'Revisa que el asunto y el mensaje no estén vacíos.',
      code: 'validation_error',
    };
  }
  return toUiError(err);
}

export type UseDraftPersistenceReturn = {
  sending: boolean;
  saving: boolean;
  error: UiError | null;
  setError: (e: UiError | null) => void;
  providerDraftId: string | null;
  setProviderDraftId: (id: string | null) => void;
  persistDraft: (mailboxId: string, accountId: string, payload: DraftPayload) => Promise<void>;
  ensureProviderDraftId: (
    mailboxId: string,
    accountId: string,
    payload: DraftPayload,
  ) => Promise<string>;
  sendEmailNow: (
    mailboxId: string,
    accountId: string,
    recipients: string[],
    subject: string,
    body: string,
  ) => Promise<boolean>;
  sendDraftNow: (
    mailboxId: string,
    accountId: string,
    providerDraftId: string,
    payloadIfDirty: DraftPayload | null,
  ) => Promise<boolean>;
  saveDraftNow: (mailboxId: string, accountId: string, payload: DraftPayload) => Promise<boolean>;
};

export default function useDraftPersistence(): UseDraftPersistenceReturn {
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<UiError | null>(null);
  const providerDraftIdRef = useRef<string | null>(null);

  const setProviderDraftId = useCallback((id: string | null) => {
    providerDraftIdRef.current = id;
  }, []);

  const persistDraft = useCallback(
    async (mailboxId: string, accountId: string, payload: DraftPayload) => {
      const existingId = providerDraftIdRef.current;
      if (existingId) {
        await updateDraft(mailboxId, accountId, existingId, payload);
      } else {
        const created = await createDraft(mailboxId, accountId, payload);
        providerDraftIdRef.current = created.provider_draft_id;
      }
    },
    [],
  );

  const ensureProviderDraftId = useCallback(
    async (mailboxId: string, accountId: string, payload: DraftPayload): Promise<string> => {
      const existingId = providerDraftIdRef.current;
      if (existingId) return existingId;
      try {
        const created = await createDraft(mailboxId, accountId, payload);
        providerDraftIdRef.current = created.provider_draft_id;
        return created.provider_draft_id;
      } catch (err) {
        setError(toUiError(err));
        throw err;
      }
    },
    [],
  );

  const sendEmailNow = useCallback(
    async (
      mailboxId: string,
      accountId: string,
      recipients: string[],
      subject: string,
      body: string,
    ): Promise<boolean> => {
      if (sending) return false;
      setError(null);
      setSending(true);
      try {
        await sendEmail(mailboxId, {
          account_id: accountId,
          subject,
          body,
          recipients,
        });
        return true;
      } catch (err) {
        const uiErr = toComposerError(err);
        const isRecipientError =
          uiErr.code === 'recipients_missing' || uiErr.code === 'email_send_error';
        setError({
          ...uiErr,
          message: isRecipientError ? 'Destinatario no encontrado' : uiErr.message,
        });
        return false;
      } finally {
        setSending(false);
      }
    },
    [sending],
  );

  const sendDraftNow = useCallback(
    async (
      mailboxId: string,
      accountId: string,
      providerDraftId: string,
      payloadIfDirty: DraftPayload | null,
    ): Promise<boolean> => {
      if (sending) return false;
      setError(null);
      setSending(true);
      try {
        if (payloadIfDirty) {
          await updateDraft(mailboxId, accountId, providerDraftId, payloadIfDirty);
        }
        await sendDraft(mailboxId, accountId, providerDraftId);
        return true;
      } catch (err) {
        setError(toComposerError(err));
        return false;
      } finally {
        setSending(false);
      }
    },
    [sending],
  );

  const saveDraftNow = useCallback(
    async (mailboxId: string, accountId: string, payload: DraftPayload): Promise<boolean> => {
      if (saving) return false;
      setError(null);
      setSaving(true);
      try {
        await persistDraft(mailboxId, accountId, payload);
        return true;
      } catch (err) {
        setError(toComposerError(err));
        return false;
      } finally {
        setSaving(false);
      }
    },
    [persistDraft, saving],
  );

  return {
    sending,
    saving,
    error,
    setError,
    providerDraftId: providerDraftIdRef.current,
    setProviderDraftId,
    persistDraft,
    ensureProviderDraftId,
    sendEmailNow,
    sendDraftNow,
    saveDraftNow,
  };
}
