import { request } from '../client/http';
import {
  accountConnectStartResponseSchema,
  accountListSchema,
  accountOutSchema,
  accountQuotaSchema,
  statusResponseSchema,
  type AccountConnectStartResponse,
  type AccountCreate,
  type AccountOut,
  type AccountQuota,
  type AccountUpdate,
  type StatusResponse,
} from '../types/dto';

export { getApiOrigin } from '../client/http';

// User-level (no mailbox prefix): counts every account the user owns across
// all their mailboxes. Accepts an optional ``signal`` like the other GETs so an
// in-flight request is aborted when the query is cancelled/unmounted.
export function getAccountQuota(signal?: AbortSignal): Promise<AccountQuota> {
  return request('/accounts/quota', { schema: accountQuotaSchema, signal });
}

export function listAccounts(mailboxId: string): Promise<AccountOut[]> {
  return request(`/mailboxes/${mailboxId}/accounts`, { schema: accountListSchema });
}

export function createAccount(mailboxId: string, payload: AccountCreate): Promise<AccountOut> {
  return request(`/mailboxes/${mailboxId}/accounts`, {
    method: 'POST',
    body: payload,
    schema: accountOutSchema,
  });
}

export function getAccount(mailboxId: string, accountId: string): Promise<AccountOut> {
  return request(`/mailboxes/${mailboxId}/accounts/${accountId}`, { schema: accountOutSchema });
}

export function updateAccount(
  mailboxId: string,
  accountId: string,
  payload: AccountUpdate,
): Promise<AccountOut> {
  return request(`/mailboxes/${mailboxId}/accounts/${accountId}`, {
    method: 'PATCH',
    body: payload,
    schema: accountOutSchema,
  });
}

export function deleteAccount(mailboxId: string, accountId: string): Promise<StatusResponse> {
  return request(`/mailboxes/${mailboxId}/accounts/${accountId}`, {
    method: 'DELETE',
    schema: statusResponseSchema,
  });
}

export function connectAccount(
  mailboxId: string,
  accountId: string,
): Promise<AccountConnectStartResponse> {
  return request(`/mailboxes/${mailboxId}/accounts/${accountId}/connect`, {
    method: 'POST',
    schema: accountConnectStartResponseSchema,
  });
}
