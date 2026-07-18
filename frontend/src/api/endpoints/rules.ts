import { request } from '../client/http';
import {
  ruleApplyStatusOutSchema,
  ruleListSchema,
  ruleOutSchema,
  statusResponseSchema,
  type RuleApplyStatusOut,
  type RuleCreate,
  type RuleOut,
  type RuleUpdate,
  type StatusResponse,
} from '../types/dto';

export function listRules(): Promise<RuleOut[]> {
  return request('/rules', { schema: ruleListSchema });
}

export function getRule(ruleId: string): Promise<RuleOut> {
  return request(`/rules/${ruleId}`, { schema: ruleOutSchema });
}

export function createRule(payload: RuleCreate): Promise<RuleOut> {
  return request('/rules', {
    method: 'POST',
    body: payload,
    schema: ruleOutSchema,
  });
}

export function updateRule(ruleId: string, payload: RuleUpdate): Promise<RuleOut> {
  return request(`/rules/${ruleId}`, {
    method: 'PATCH',
    body: payload,
    schema: ruleOutSchema,
  });
}

export function deleteRule(ruleId: string): Promise<StatusResponse> {
  return request(`/rules/${ruleId}`, {
    method: 'DELETE',
    schema: statusResponseSchema,
  });
}

// Enqueue the "apply to existing" background job (full scan from scratch,
// idempotent) and return its initial status.
export function applyRule(ruleId: string): Promise<RuleApplyStatusOut> {
  return request(`/rules/${ruleId}/apply`, {
    method: 'POST',
    schema: ruleApplyStatusOutSchema,
  });
}

// Local-only progress poll for the "apply to existing" job. Accepts a signal so
// an in-flight poll is aborted when the query is cancelled/unmounted.
export function getRuleApplyStatus(
  ruleId: string,
  signal?: AbortSignal,
): Promise<RuleApplyStatusOut> {
  return request(`/rules/${ruleId}/apply-status`, {
    schema: ruleApplyStatusOutSchema,
    signal,
  });
}
