import { request } from '../client/http';
import { EMAILS_PAGE_SIZE } from '../../lib/constants';
import {
  emailPageSchema,
  statusResponseSchema,
  virtualMailboxListSchema,
  virtualMailboxOutSchema,
  type EmailPage,
  type StatusResponse,
  type VirtualMailboxCreate,
  type VirtualMailboxOut,
  type VirtualMailboxUpdate,
} from '../types/dto';

export function listVirtualMailboxes(): Promise<VirtualMailboxOut[]> {
  return request('/virtual-mailboxes', { schema: virtualMailboxListSchema });
}

export function getVirtualMailbox(virtualMailboxId: string): Promise<VirtualMailboxOut> {
  return request(`/virtual-mailboxes/${virtualMailboxId}`, {
    schema: virtualMailboxOutSchema,
  });
}

export function createVirtualMailbox(payload: VirtualMailboxCreate): Promise<VirtualMailboxOut> {
  return request('/virtual-mailboxes', {
    method: 'POST',
    body: payload,
    schema: virtualMailboxOutSchema,
  });
}

export function updateVirtualMailbox(
  virtualMailboxId: string,
  payload: VirtualMailboxUpdate,
): Promise<VirtualMailboxOut> {
  return request(`/virtual-mailboxes/${virtualMailboxId}`, {
    method: 'PATCH',
    body: payload,
    schema: virtualMailboxOutSchema,
  });
}

export function deleteVirtualMailbox(virtualMailboxId: string): Promise<StatusResponse> {
  return request(`/virtual-mailboxes/${virtualMailboxId}`, {
    method: 'DELETE',
    schema: statusResponseSchema,
  });
}

export type ListVirtualMailboxEmailsOptions = {
  q?: string;
  page?: number;
  signal?: AbortSignal;
};

export function listVirtualMailboxEmails(
  virtualMailboxId: string,
  options: ListVirtualMailboxEmailsOptions = {},
): Promise<EmailPage> {
  const params = new URLSearchParams();
  if (options.q !== undefined && options.q.length > 0) params.set('q', options.q);
  const limit = EMAILS_PAGE_SIZE;
  const offset = ((options.page ?? 1) - 1) * limit;
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return request(`/virtual-mailboxes/${virtualMailboxId}/emails?${params}`, {
    schema: emailPageSchema,
    signal: options.signal,
  });
}
