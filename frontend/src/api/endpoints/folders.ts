import { request } from '../client/http';
import { EMAILS_PAGE_SIZE } from '../../lib/constants';
import {
  emailFoldersOutSchema,
  emailPageSchema,
  folderListSchema,
  folderOutSchema,
  statusResponseSchema,
  type EmailFoldersOut,
  type EmailPage,
  type FolderCreate,
  type FolderOut,
  type FolderUpdate,
  type StatusResponse,
} from '../types/dto';

export function listFolders(signal?: AbortSignal): Promise<FolderOut[]> {
  return request('/folders', { schema: folderListSchema, signal });
}

export function getFolder(folderId: string): Promise<FolderOut> {
  return request(`/folders/${folderId}`, { schema: folderOutSchema });
}

export function createFolder(payload: FolderCreate): Promise<FolderOut> {
  return request('/folders', {
    method: 'POST',
    body: payload,
    schema: folderOutSchema,
  });
}

export function updateFolder(folderId: string, payload: FolderUpdate): Promise<FolderOut> {
  return request(`/folders/${folderId}`, {
    method: 'PATCH',
    body: payload,
    schema: folderOutSchema,
  });
}

export function deleteFolder(folderId: string): Promise<StatusResponse> {
  return request(`/folders/${folderId}`, {
    method: 'DELETE',
    schema: statusResponseSchema,
  });
}

export type ListFolderEmailsOptions = {
  q?: string;
  page?: number;
  signal?: AbortSignal;
};

export function listFolderEmails(
  folderId: string,
  options: ListFolderEmailsOptions = {},
): Promise<EmailPage> {
  const params = new URLSearchParams();
  if (options.q !== undefined && options.q.length > 0) params.set('q', options.q);
  const limit = EMAILS_PAGE_SIZE;
  const offset = ((options.page ?? 1) - 1) * limit;
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return request(`/folders/${folderId}/emails?${params}`, {
    schema: emailPageSchema,
    signal: options.signal,
  });
}

// Per-email assignment. Mirrors ``setFavorite``: the URL carries the email's
// own ``account_id`` AND ``provider_message_id`` (NOT the route mailbox) so the
// backend can route the provider label/category call. Provider-First on the
// server; the response is the email's full folder list AFTER the operation.
export function assignFolder(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  folderId: string,
): Promise<EmailFoldersOut> {
  return request(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${providerMessageId}/folders`,
    {
      method: 'POST',
      body: { folder_id: folderId },
      schema: emailFoldersOutSchema,
    },
  );
}

export function unassignFolder(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  folderId: string,
): Promise<EmailFoldersOut> {
  return request(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${providerMessageId}/folders/${folderId}`,
    {
      method: 'DELETE',
      schema: emailFoldersOutSchema,
    },
  );
}
