/**
 * Integration tests for useRuleApplyStatus (MSW at the network boundary).
 *
 * Mirrors useBackfillStatus: ``refetchInterval`` is gated by ``data.active``
 * (the poll goes idle when the job finishes), and progressive invalidation
 * repaints the folder listings when ``processed_count`` grows or the job just
 * finished. Polling is driven explicitly with ``refetchQueries`` rather than
 * real timers, and invalidation is observed via a spy.
 */

import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import useRuleApplyStatus from './useRuleApplyStatus';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';
const RULE_ID = 'r1';

let queryClient: QueryClient;
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function status(processed: number, active: boolean, s = 'running') {
  return HttpResponse.json({ status: s, processed_count: processed, active });
}

beforeEach(() => {
  queryClient = createTestQueryClient();
});

afterEach(() => {
  server.resetHandlers();
});

describe('useRuleApplyStatus', () => {
  it('exposes the running status and processed count', async () => {
    server.use(http.get(`${API_BASE}/rules/:ruleId/apply-status`, () => status(120, true)));
    const { result } = renderHook(() => useRuleApplyStatus(RULE_ID), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.active).toBe(true));
    expect(result.current.processedCount).toBe(120);
    expect(result.current.status).toBe('running');
  });

  it('does not invalidate listings on the first fetch (no previous counter)', async () => {
    // Default handler: status "none", inactive. A first fetch must not repaint.
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useRuleApplyStatus(RULE_ID), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.active).toBe(false));
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['folder-emails'] });
  });

  it('invalidates the folder listings when the processed count grows', async () => {
    let call = 0;
    server.use(
      http.get(`${API_BASE}/rules/:ruleId/apply-status`, () => {
        call += 1;
        return status(call === 1 ? 10 : 25, true);
      }),
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useRuleApplyStatus(RULE_ID), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.processedCount).toBe(10));

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['rule-apply-status', RULE_ID] });
    });
    await waitFor(() => expect(result.current.processedCount).toBe(25));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['folder-emails'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['emails'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['virtual-mailbox-emails'] });
  });

  it('invalidates once when the job just finished (active true → false)', async () => {
    let call = 0;
    server.use(
      http.get(`${API_BASE}/rules/:ruleId/apply-status`, () => {
        call += 1;
        return call === 1 ? status(10, true) : status(10, false, 'completed');
      }),
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useRuleApplyStatus(RULE_ID), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.active).toBe(true));

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['rule-apply-status', RULE_ID] });
    });
    await waitFor(() => expect(result.current.active).toBe(false));
    // The finish transition repaints the final classification even without growth.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['folders'] });
  });
});
