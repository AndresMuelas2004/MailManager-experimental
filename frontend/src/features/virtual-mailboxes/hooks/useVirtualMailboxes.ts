import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createVirtualMailbox,
  deleteVirtualMailbox,
  listVirtualMailboxes,
  updateVirtualMailbox,
} from '../../../api/endpoints/virtualMailboxes';
import { toUiError } from '../../../api/client/errors';
import type {
  VirtualMailboxCreate,
  VirtualMailboxOut,
  VirtualMailboxUpdate,
} from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseVirtualMailboxesReturn = {
  virtualMailboxes: VirtualMailboxOut[];
  loading: boolean;
  mutating: boolean;
  error: UiError | null;
  create: (payload: VirtualMailboxCreate) => Promise<VirtualMailboxOut>;
  update: (virtualMailboxId: string, payload: VirtualMailboxUpdate) => Promise<VirtualMailboxOut>;
  remove: (virtualMailboxId: string) => Promise<void>;
  refresh: () => Promise<void>;
};

export default function useVirtualMailboxes(): UseVirtualMailboxesReturn {
  const queryClient = useQueryClient();
  const queryKey = ['virtual-mailboxes'] as const;

  const listQuery = useQuery({
    queryKey,
    queryFn: () => listVirtualMailboxes(),
  });

  const invalidate = useCallback(() => {
    return queryClient.invalidateQueries({ queryKey });
  }, [queryClient]);

  const createMutation = useMutation({
    mutationFn: (payload: VirtualMailboxCreate) => createVirtualMailbox(payload),
    onSuccess: invalidate,
  });

  const updateMutation = useMutation({
    mutationFn: ({
      virtualMailboxId,
      payload,
    }: {
      virtualMailboxId: string;
      payload: VirtualMailboxUpdate;
    }) => updateVirtualMailbox(virtualMailboxId, payload),
    onSuccess: (_data, { virtualMailboxId }) => {
      invalidate();
      queryClient.invalidateQueries({
        queryKey: ['virtual-mailbox', virtualMailboxId],
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (virtualMailboxId: string) => deleteVirtualMailbox(virtualMailboxId),
    onSuccess: (_data, virtualMailboxId) => {
      invalidate();
      queryClient.removeQueries({
        queryKey: ['virtual-mailbox', virtualMailboxId],
      });
    },
  });

  const create = useCallback(
    async (payload: VirtualMailboxCreate) => createMutation.mutateAsync(payload),
    [createMutation],
  );
  const update = useCallback(
    async (virtualMailboxId: string, payload: VirtualMailboxUpdate) =>
      updateMutation.mutateAsync({ virtualMailboxId, payload }),
    [updateMutation],
  );
  const remove = useCallback(
    async (virtualMailboxId: string) => {
      await deleteMutation.mutateAsync(virtualMailboxId);
    },
    [deleteMutation],
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
          : null;

  return {
    virtualMailboxes: listQuery.data ?? [],
    loading: listQuery.isLoading,
    mutating: createMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
    error,
    create,
    update,
    remove,
    refresh,
  };
}
