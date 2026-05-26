import { request } from '../client/http';
import {
  emailMetadataListSchema,
  virtualMailboxListSchema,
  virtualMailboxOutSchema,
  type EmailMetadataOut,
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

export function deleteVirtualMailbox(virtualMailboxId: string): Promise<unknown> {
  return request(`/virtual-mailboxes/${virtualMailboxId}`, {
    method: 'DELETE',
  });
}

export type ListVirtualMailboxEmailsOptions = {
  q?: string;
  signal?: AbortSignal;
};

export function listVirtualMailboxEmails(
  virtualMailboxId: string,
  options: ListVirtualMailboxEmailsOptions = {},
): Promise<EmailMetadataOut[]> {
  const params = new URLSearchParams();
  if (options.q !== undefined && options.q.length > 0) params.set('q', options.q);
  const qs = params.toString();
  return request(`/virtual-mailboxes/${virtualMailboxId}/emails${qs ? `?${qs}` : ''}`, {
    schema: emailMetadataListSchema,
    signal: options.signal,
  });
}
