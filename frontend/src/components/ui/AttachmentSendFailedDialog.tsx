import Modal from '../common/Modal';
import { useTranslation } from '../../lib/i18n';
import type { FailedAttachmentDetail } from '../../api/types/dto';

type Props = {
  open: boolean;
  failedAttachments: FailedAttachmentDetail[];
  busy?: boolean;
  onRetry: () => void;
  onRemoveFailedAndRetry: () => void;
  onClose: () => void;
};

/**
 * Dialog for the ``attachment_send_failed`` flow (D-27).
 *
 * Two recovery paths surfaced as side-by-side primary buttons:
 * - Retry the send as-is (Outlook will skip the parts already
 *   uploaded thanks to ``provider_attachment_id``).
 * - Drop the failed attachments and retry without them.
 */
export default function AttachmentSendFailedDialog({
  open,
  failedAttachments,
  busy,
  onRetry,
  onRemoveFailedAndRetry,
  onClose,
}: Props) {
  const { t } = useTranslation();
  return (
    <Modal
      open={open}
      onClose={onClose}
      ariaLabel={t('attachmentSendFailed.ariaLabel')}
      widthClass="max-w-md"
    >
      <div className="space-y-3 px-6 py-5">
        <h2 className="text-base font-semibold text-zinc-900">{t('attachmentSendFailed.title')}</h2>
        <p className="text-[13px] text-zinc-700">{t('attachmentSendFailed.description')}</p>
        {failedAttachments.length > 0 && (
          <ul className="max-h-40 overflow-auto rounded border border-zinc-200 bg-zinc-50 px-3 py-2 text-[12px] text-zinc-700">
            {failedAttachments.map((item) => (
              <li
                key={item.draft_attachment_id}
                className="flex items-center justify-between gap-2 py-0.5"
              >
                <span className="truncate">{item.filename}</span>
                <span className="ml-2 shrink-0 text-zinc-500">{item.reason}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
          <button
            type="button"
            disabled={busy}
            onClick={onClose}
            className="rounded-md border border-zinc-200 px-3 py-1.5 text-[13px] text-zinc-700 hover:bg-zinc-50 disabled:opacity-60"
          >
            {t('common.close')}
          </button>
          <button
            type="button"
            disabled={busy || failedAttachments.length === 0}
            onClick={onRemoveFailedAndRetry}
            className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-800 hover:bg-zinc-50 disabled:opacity-60"
          >
            {t('attachmentSendFailed.removeFailedAndSend')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onRetry}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {t('attachmentSendFailed.retry')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
