import AttachmentCard from './AttachmentCard';
import { useTranslation } from '../../../lib/i18n';
import type { UseAttachmentDownloaderReturn } from '../hooks/useAttachmentDownloader';
import type { AttachmentMetadata } from '../../../api/types/dto';

type Props = {
  attachments: AttachmentMetadata[];
  downloader: UseAttachmentDownloaderReturn;
};

// Presentational list of downloadable attachments. Extracted from
// ``EmailViewer`` so the conversation viewer's per-message body
// (``ConversationMessageBody``) can render the same list. The download
// queue / browser-trigger logic stays in the ``downloader`` prop (a hook
// instantiated by the container), keeping this component fetch-free.
export default function AttachmentsList({ attachments, downloader }: Props) {
  const { t } = useTranslation();
  if (attachments.length === 0) {
    return null;
  }

  return (
    <section className="border-t border-zinc-200 px-6 py-4">
      <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wider text-zinc-500">
        {t('attachments.title', { count: attachments.length })}
      </h3>
      <ul className="grid gap-2 sm:grid-cols-2">
        {attachments.map((attachment) => {
          const status = downloader.status(attachment.attachment_id);
          return (
            <li key={attachment.attachment_id}>
              <AttachmentCard
                attachment={attachment}
                status={status}
                onDownload={() => downloader.start(attachment.attachment_id, attachment.filename)}
                onCancel={() => downloader.cancel(attachment.attachment_id)}
              />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
