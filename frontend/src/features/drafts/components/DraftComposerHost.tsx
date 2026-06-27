import { useEffect, useState } from 'react';

import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import AttachmentSendFailedDialog from '../../../components/ui/AttachmentSendFailedDialog';
import CloseComposerDialog from '../../../components/ui/CloseComposerDialog';
import ComposeOverlay from '../../../components/ui/ComposeOverlay';
import useDebounce from '../../../lib/hooks/useDebounce';
import useDraftComposer from '../hooks/useDraftComposer';
import useRecipientSuggestions from '../hooks/useRecipientSuggestions';

type Props = {
  mailboxId: string | null;
};

const RECIPIENT_DEBOUNCE_MS = 250;

// Headless container that bridges the drafts feature into the cross-cutting
// DraftComposerContext provided by `app/providers/DraftComposerProvider`.
// Architectural exception to features/CLAUDE.md §5.1 ("components do not call
// hooks that fetch data"). Documented in `frontend/frontend_guide.md` §1.1 —
// the host's job is to wire the hook into the context the layout consumes
// via the register pattern, keeping `features/` imports out of `app/providers/`.
// Mount once inside the layout scope where `mailboxId` is known
// (MailboxLayoutPage).
export default function DraftComposerHost({ mailboxId }: Props) {
  const composer = useDraftComposer(mailboxId);
  const { __register } = useDraftComposerContext();

  // Recipient autocomplete: the active fragment of whichever recipient field
  // is being edited, debounced before it drives the suggestions hook.
  // Adding this data hook here rides the SAME documented exception as
  // useDraftComposer (frontend_guide §1.1) — the host is the only
  // ``features/<x>/components/`` allowed to call a fetching hook.
  // ``recipientQuery`` persists across composer open/close; that is harmless
  // because nothing reads ``recipientSuggestions`` while the overlay is
  // unmounted, and the first keystroke on reopen overwrites the fragment.
  const [recipientQuery, setRecipientQuery] = useState('');
  const debouncedRecipientQuery = useDebounce(recipientQuery, RECIPIENT_DEBOUNCE_MS);
  const { suggestions: recipientSuggestions, loading: recipientSuggestionsLoading } =
    useRecipientSuggestions(debouncedRecipientQuery);

  useEffect(() => {
    __register({
      openForNewEmail: composer.openForNewEmail,
      openForNewDraft: composer.openForNewDraft,
      openForEditDraft: composer.openForEditDraft,
      openForReply: composer.openForReply,
      openForReplyAll: composer.openForReplyAll,
      openForForward: composer.openForForward,
      setRefreshCallback: composer.setRefreshCallback,
    });
    return () => __register(null);
  }, [
    __register,
    composer.openForNewEmail,
    composer.openForNewDraft,
    composer.openForEditDraft,
    composer.openForReply,
    composer.openForReplyAll,
    composer.openForForward,
    composer.setRefreshCallback,
  ]);

  // Esc closes the composer through the SAME flow as the ✕ button
  // (``closeWithX`` → the unsaved-changes dialog when dirty, a direct close
  // when clean). Registered only while the overlay is open AND no nested
  // dialog is showing, so the close-confirmation / send-failed modals keep
  // their own ``Modal`` Esc handling. ``defaultPrevented`` leaves the composer
  // open when a child already consumed Escape (recipient autocomplete
  // dropdown, link popover).
  useEffect(() => {
    if (!composer.open || composer.closeDialogOpen || composer.sendFailedOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) composer.closeWithX();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [composer.open, composer.closeDialogOpen, composer.sendFailedOpen, composer.closeWithX]);

  return (
    <>
      {composer.open && composer.mode && (
        <ComposeOverlay
          mode={composer.mode}
          accounts={composer.accounts}
          selectedAccountId={composer.accountId}
          onSelectedAccountChange={composer.setAccountId}
          to={composer.to}
          onToChange={composer.setTo}
          cc={composer.cc}
          onCcChange={composer.setCc}
          bcc={composer.bcc}
          onBccChange={composer.setBcc}
          subject={composer.subject}
          onSubjectChange={composer.setSubject}
          body={composer.body}
          onBodyChange={composer.setBody}
          sending={composer.sending}
          saving={composer.saving}
          error={composer.error}
          recipientError={composer.recipientError}
          bodyError={composer.bodyError}
          canSendEmail={composer.canSendEmail}
          canSaveDraft={composer.canSaveDraft}
          canSendDraft={composer.canSendDraft}
          onSendEmail={composer.handleSendEmail}
          onSaveDraft={composer.handleSaveDraft}
          onSendDraft={composer.handleSendDraft}
          onClose={composer.closeWithX}
          attachmentsEnabled={composer.attachmentsEnabled}
          accountSelectorLocked={composer.accountSelectorLocked}
          attachmentChips={composer.attachmentChips}
          attachmentTotalSize={composer.attachmentTotalSize}
          onAddFiles={composer.addAttachmentFiles}
          onRemoveAttachment={composer.removeAttachmentChip}
          recipientSuggestions={recipientSuggestions}
          recipientSuggestionsLoading={recipientSuggestionsLoading}
          onRecipientQueryChange={setRecipientQuery}
        />
      )}
      <CloseComposerDialog
        open={composer.closeDialogOpen}
        busy={composer.saving || composer.sending}
        onSaveAndClose={composer.confirmCloseSave}
        onDiscard={composer.confirmCloseDiscard}
        onCancel={composer.cancelClose}
      />
      <AttachmentSendFailedDialog
        open={composer.sendFailedOpen}
        failedAttachments={composer.failedAttachments}
        busy={composer.sending}
        onRetry={composer.retrySend}
        onRemoveFailedAndRetry={composer.removeFailedAndRetrySend}
        onClose={composer.closeSendFailedDialog}
      />
    </>
  );
}
