import { request, requestBlob, requestUploadWithProgress } from '../client/http';
import {
  draftAttachmentResponseSchema,
  statusResponseSchema,
  type DraftAttachmentResponse,
  type StatusResponse,
} from '../types/dto';

/**
 * Download a single attachment binary. Resolves to a Blob plus the
 * filename declared by the backend ``Content-Disposition`` header. The
 * caller (`useDownloadQueue` consumer) creates an object URL and fires
 * the browser download — keeping the UI in control of the download
 * lifecycle (spinner, retry, cancel).
 */
export function downloadEmailAttachment(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  attachmentId: string,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  return requestBlob(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${providerMessageId}/attachments/${attachmentId}`,
    {
      method: 'GET',
      signal,
      fallbackFilename: `attachment-${attachmentId}`,
    },
  );
}

/**
 * Upload a new attachment to a draft (D-07 lazy push). Provides real
 * upload progress via ``requestUploadWithProgress``. The local chip
 * stays in ``uploading`` state until this resolves.
 */
export function addDraftAttachment(
  mailboxId: string,
  accountId: string,
  providerDraftId: string,
  file: File,
  options: {
    onProgress?: (percentage: number) => void;
    signal?: AbortSignal;
  } = {},
): Promise<DraftAttachmentResponse> {
  const formData = new FormData();
  formData.append('file', file, file.name);
  return requestUploadWithProgress(
    `/mailboxes/${mailboxId}/accounts/${accountId}/drafts/${providerDraftId}/attachments`,
    formData,
    {
      onProgress: options.onProgress,
      signal: options.signal,
      schema: draftAttachmentResponseSchema,
    },
  );
}

export function removeDraftAttachment(
  mailboxId: string,
  accountId: string,
  providerDraftId: string,
  draftAttachmentId: string,
): Promise<StatusResponse> {
  return request(
    `/mailboxes/${mailboxId}/accounts/${accountId}/drafts/${providerDraftId}/attachments/${draftAttachmentId}`,
    {
      method: 'DELETE',
      schema: statusResponseSchema,
    },
  );
}
