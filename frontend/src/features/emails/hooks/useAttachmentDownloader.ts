import { useCallback } from 'react';

import { downloadEmailAttachment } from '../../../api/endpoints/attachments';
import useDownloadQueue, { type DownloadStatus } from './useDownloadQueue';

type UseAttachmentDownloaderArgs = {
  mailboxId: string;
  accountId: string;
  providerMessageId: string;
};

export type UseAttachmentDownloaderReturn = {
  status: (attachmentId: string) => DownloadStatus;
  start: (attachmentId: string, filename: string) => void;
  cancel: (attachmentId: string) => void;
};

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Bridge hook that combines the bounded download queue with the
 * browser-side trigger logic. Components in
 * ``features/emails/components/`` consume this instead of touching
 * ``api/endpoints/`` directly (which would violate the components
 * import boundary).
 */
export default function useAttachmentDownloader(
  args: UseAttachmentDownloaderArgs,
): UseAttachmentDownloaderReturn {
  const queue = useDownloadQueue();

  const start = useCallback(
    (attachmentId: string, filename: string) => {
      queue
        .enqueue(attachmentId, () =>
          downloadEmailAttachment(
            args.mailboxId,
            args.accountId,
            args.providerMessageId,
            attachmentId,
            filename,
          ),
        )
        // ``resolved`` is the name ``requestBlob`` settled on (header first,
        // propagated ``filename`` as fallback). Renamed to avoid shadowing the
        // ``filename`` argument of ``start``.
        .then(({ blob, filename: resolved }) => triggerBrowserDownload(blob, resolved))
        .catch(() => {
          /* errors surface via queue.status — visible state lives there */
        });
    },
    [args.accountId, args.mailboxId, args.providerMessageId, queue],
  );

  return { status: queue.status, start, cancel: queue.cancel };
}
