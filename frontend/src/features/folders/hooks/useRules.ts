import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  applyRule,
  createRule,
  deleteRule,
  listRules,
  updateRule,
} from '../../../api/endpoints/rules';
import { toUiError } from '../../../api/client/errors';
import type { RuleApplyStatusOut, RuleCreate, RuleOut, RuleUpdate } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseRulesReturn = {
  rules: RuleOut[];
  loading: boolean;
  mutating: boolean;
  error: UiError | null;
  create: (payload: RuleCreate) => Promise<RuleOut>;
  update: (ruleId: string, payload: RuleUpdate) => Promise<RuleOut>;
  remove: (ruleId: string) => Promise<void>;
  apply: (ruleId: string) => Promise<RuleApplyStatusOut>;
  refresh: () => Promise<void>;
};

// One hook for the whole rules surface (mirrors ``useVirtualMailboxes``): list
// keyed ``['rules']`` + create/update/remove/apply. ``apply`` triggers the
// "apply to existing" job and re-arms its status poll by invalidating
// ``['rule-apply-status', ruleId]`` (the same trap ``useBackfillStatus`` closes
// via ``addAccount``: the poll goes idle once ``active`` is false, so a freshly
// enqueued job needs a forced refetch to see ``active:true`` again).
export default function useRules(): UseRulesReturn {
  const queryClient = useQueryClient();
  const queryKey = ['rules'] as const;

  const listQuery = useQuery({
    queryKey,
    queryFn: () => listRules(),
  });

  const invalidate = useCallback(() => {
    return queryClient.invalidateQueries({ queryKey });
  }, [queryClient]);

  const createMutation = useMutation({
    mutationFn: (payload: RuleCreate) => createRule(payload),
    onSuccess: (data) => {
      invalidate();
      // ``apply_to_existing:true`` also enqueued the job — re-arm its poll.
      queryClient.invalidateQueries({ queryKey: ['rule-apply-status', data.rule_id] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: string; payload: RuleUpdate }) =>
      updateRule(ruleId, payload),
    onSuccess: (_data, { ruleId }) => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: ['rule-apply-status', ruleId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ruleId: string) => deleteRule(ruleId),
    onSuccess: (_data, ruleId) => {
      invalidate();
      queryClient.removeQueries({ queryKey: ['rule-apply-status', ruleId] });
    },
  });

  const applyMutation = useMutation({
    mutationFn: (ruleId: string) => applyRule(ruleId),
    onSuccess: (_data, ruleId) => {
      // Re-arm the status poll so it picks up the freshly enqueued job.
      queryClient.invalidateQueries({ queryKey: ['rule-apply-status', ruleId] });
    },
  });

  const create = useCallback(
    async (payload: RuleCreate) => createMutation.mutateAsync(payload),
    [createMutation],
  );
  const update = useCallback(
    async (ruleId: string, payload: RuleUpdate) => updateMutation.mutateAsync({ ruleId, payload }),
    [updateMutation],
  );
  const remove = useCallback(
    async (ruleId: string) => {
      await deleteMutation.mutateAsync(ruleId);
    },
    [deleteMutation],
  );
  const apply = useCallback(
    async (ruleId: string) => applyMutation.mutateAsync(ruleId),
    [applyMutation],
  );

  const refresh = useCallback(async () => {
    await invalidate();
  }, [invalidate]);

  const error = listQuery.error
    ? toUiError(listQuery.error)
    : createMutation.error
      ? toUiError(createMutation.error)
      : updateMutation.error
        ? toUiError(updateMutation.error)
        : deleteMutation.error
          ? toUiError(deleteMutation.error)
          : applyMutation.error
            ? toUiError(applyMutation.error)
            : null;

  return {
    rules: listQuery.data ?? [],
    loading: listQuery.isLoading,
    mutating:
      createMutation.isPending ||
      updateMutation.isPending ||
      deleteMutation.isPending ||
      applyMutation.isPending,
    error,
    create,
    update,
    remove,
    apply,
    refresh,
  };
}
