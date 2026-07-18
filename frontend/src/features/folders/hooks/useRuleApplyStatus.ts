import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { getRuleApplyStatus } from '../../../api/endpoints/rules';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { RuleApplyStatusOut } from '../../../api/types/dto';

const POLL_INTERVAL_MS = 2000;

type UseRuleApplyStatusReturn = {
  status: RuleApplyStatusOut['status'];
  processedCount: number;
  active: boolean;
  // A poll failure is surfaced here and never blocks the view.
  error: UiError | null;
};

/**
 * Poll the "apply to existing" status for one rule while its job runs,
 * repainting the folder listings as the counter grows. Mirrors
 * ``useBackfillStatus``: ``refetchInterval`` gated by ``data.active`` (the poll
 * goes idle once the job finishes), and ``useRules.apply`` invalidates
 * ``['rule-apply-status', ruleId]`` to re-arm the interval after a fresh apply.
 */
export default function useRuleApplyStatus(ruleId: string): UseRuleApplyStatusReturn {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['rule-apply-status', ruleId] as const,
    queryFn: ({ signal }) => getRuleApplyStatus(ruleId, signal),
    enabled: ruleId.length > 0,
    refetchInterval: (q) => (q.state.data?.active ? POLL_INTERVAL_MS : false),
  });

  const data = query.data;

  // Progressive invalidation (self-throttled): repaint the listings when the
  // processed_count grew since the previous tick — OR when the job just
  // finished (active true→false: paint the final classification). The growth
  // gate IS the throttle. None of these keys is ['rule-apply-status'], so the
  // poll never feeds itself.
  const prevProcessed = useRef<number | null>(null);
  const prevActive = useRef(false);
  useEffect(() => {
    if (!data) return;
    const grew = prevProcessed.current !== null && data.processed_count > prevProcessed.current;
    const justFinished = prevActive.current && !data.active;
    if (grew || justFinished) {
      void queryClient.invalidateQueries({ queryKey: ['folder-emails'] });
      void queryClient.invalidateQueries({ queryKey: ['folders'] });
      void queryClient.invalidateQueries({ queryKey: ['emails'] });
      void queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    }
    prevProcessed.current = data.processed_count;
    prevActive.current = data.active;
  }, [data, queryClient]);

  return {
    status: data?.status ?? 'none',
    processedCount: data?.processed_count ?? 0,
    active: data?.active ?? false,
    error: query.error ? toUiError(query.error) : null,
  };
}
