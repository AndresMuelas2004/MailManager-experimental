import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import useMailboxList from '../hooks/useMailboxList';
import useRenameMailbox from '../hooks/useRenameMailbox';
import useDeleteMailbox from '../hooks/useDeleteMailbox';
import MailboxSettingsRow from '../components/MailboxSettingsRow';
import ConfirmModal from '../../../components/common/ConfirmModal';
import { useTranslation } from '../../../lib/i18n';
import type { MailboxOut } from '../../../api/types/dto';

export default function MailboxesSettingsPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const { mailboxes } = useMailboxList(mailboxId!);
  const rename = useRenameMailbox();
  const del = useDeleteMailbox();

  const [pendingDelete, setPendingDelete] = useState<MailboxOut | null>(null);

  const busy = rename.loading || del.loading;
  const combinedError = rename.error || del.error;

  const handleRename = (id: string, displayName: string) => {
    void rename.rename({ mailboxId: id, displayName });
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    const deletedId = pendingDelete.mailbox_id;
    const ok = await del.remove(deletedId);
    setPendingDelete(null);
    if (!ok) return;

    // If the user deleted the mailbox currently selected in the URL, move them
    // to a surviving mailbox's inbox; if none remain, go to the index, which
    // routes to "create mailbox" when the user has no mailboxes left.
    if (deletedId === mailboxId) {
      const survivor = mailboxes.find((m) => m.mailbox_id !== deletedId);
      if (survivor) navigate(`/m/${survivor.mailbox_id}/inbox`);
      else navigate('/');
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
          {t('mailboxesSettings.title')}
        </h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{t('mailboxesSettings.subtitle')}</p>
      </div>

      {combinedError && (
        <div className="px-8 pb-2 text-sm text-red-600">{combinedError.message}</div>
      )}

      {mailboxes.length === 0 ? (
        <div className="px-8 py-10 text-center text-sm text-zinc-400">
          {t('mailboxesSettings.empty')}
        </div>
      ) : (
        <ul className="flex flex-col">
          {mailboxes.map((mailbox) => (
            <MailboxSettingsRow
              key={mailbox.mailbox_id}
              displayName={mailbox.display_name}
              busy={busy}
              onRename={(displayName) => handleRename(mailbox.mailbox_id, displayName)}
              onRequestDelete={() => setPendingDelete(mailbox)}
            />
          ))}
        </ul>
      )}

      {pendingDelete && (
        <ConfirmModal
          title={t('mailboxesSettings.confirmDeleteTitle')}
          description={t('mailboxesSettings.confirmDeleteDescription')}
          confirmLabel={t('common.delete')}
          cancelLabel={t('common.cancel')}
          onCancel={() => setPendingDelete(null)}
          onConfirm={handleConfirmDelete}
        />
      )}
    </div>
  );
}
