/**
 * Integration tests for useRules (MSW at the network boundary).
 *
 * Real endpoints + Zod + React Query cache; only the HTTP response is
 * synthesized. The apply flow re-arms the status poll by invalidating
 * ``['rule-apply-status', ruleId]`` — the same trap ``useBackfillStatus`` closes
 * — observed via a spy on the shared ``queryClient.invalidateQueries``.
 */

import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import useRules from './useRules';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';

let queryClient: QueryClient;
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  queryClient = createTestQueryClient();
});

afterEach(() => {
  server.resetHandlers();
});

describe('useRules', () => {
  it('exposes the rule list from the server', async () => {
    server.use(
      http.get(`${API_BASE}/rules`, () =>
        HttpResponse.json([
          {
            rule_id: 'r1',
            owner_user_id: 'u1',
            name: null,
            is_enabled: true,
            match_from_email: 'boss@example.com',
            match_subject_contains: null,
            target_folder_id: 'f1',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]),
      ),
    );
    const { result } = renderHook(() => useRules(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.rules).toHaveLength(1);
    expect(result.current.rules[0].match_from_email).toBe('boss@example.com');
  });

  it('creates a rule and invalidates ["rules"]', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useRules(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.create({
        match_from_email: 'boss@example.com',
        target_folder_id: 'f1',
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['rules'] });
  });

  it('apply re-arms the status poll for the applied rule', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useRules(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      const status = await result.current.apply('r1');
      expect(status.active).toBe(true);
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['rule-apply-status', 'r1'] });
  });

  it('surfaces a rule error through toUiError', async () => {
    server.use(
      http.post(`${API_BASE}/rules`, () =>
        HttpResponse.json(
          { error: { code: 'rule_validation_error', message: 'no condition' } },
          { status: 422 },
        ),
      ),
    );
    const { result } = renderHook(() => useRules(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await expect(result.current.create({ target_folder_id: 'f1' })).rejects.toBeTruthy();
    });
    await waitFor(() => expect(result.current.error?.code).toBe('rule_validation_error'));
  });
});
