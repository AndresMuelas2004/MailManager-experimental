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
 *
 * `filename` is the real attachment name (with extension) taken from the
 * email's attachment metadata. It is forwarded as `fallbackFilename` so
 * the download keeps its correct name+extension even when the browser
 * cannot read `Content-Disposition` (cross-origin without CORS exposing
 * the header) — defence in depth against the download-name bug.
 */
export function downloadEmailAttachment(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  attachmentId: string,
  filename: string,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  return requestBlob(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${providerMessageId}/attachments/${attachmentId}`,
    {
      method: 'GET',
      signal,
      fallbackFilename: filename || `attachment-${attachmentId}`,
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
