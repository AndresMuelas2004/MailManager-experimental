import { useEffect } from 'react';

import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import AttachmentSendFailedDialog from '../../../components/ui/AttachmentSendFailedDialog';
import CloseComposerDialog from '../../../components/ui/CloseComposerDialog';
import ComposeOverlay from '../../../components/ui/ComposeOverlay';
import useDraftComposer from '../hooks/useDraftComposer';

type Props = {
  mailboxId: string | null;
};

// Headless container that bridges the drafts feature into the cross-cutting
// DraftComposerContext provided by `app/providers/DraftComposerProvider`.
// Architectural exception to features/CLAUDE.md §5.1 ("components do not call
// hooks that fetch data") — this component's whole job is to wire the hook
// into a context the layout consumes via the register pattern, keeping the
// `features/` imports out of `app/providers/`. Mount once inside the layout
// scope where `mailboxId` is known (MailboxLayoutPage).
export default function DraftComposerHost({ mailboxId }: Props) {
  const composer = useDraftComposer(mailboxId);
  const { __register } = useDraftComposerContext();

  useEffect(() => {
    __register({
      openForNewEmail: composer.openForNewEmail,
      openForNewDraft: composer.openForNewDraft,
      openForEditDraft: composer.openForEditDraft,
      setRefreshCallback: composer.setRefreshCallback,
    });
    return () => __register(null);
  }, [
    __register,
    composer.openForNewEmail,
    composer.openForNewDraft,
    composer.openForEditDraft,
    composer.setRefreshCallback,
  ]);

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
