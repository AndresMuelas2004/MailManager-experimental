import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createFolder,
  deleteFolder,
  listFolders,
  updateFolder,
} from '../../../api/endpoints/folders';
import { toUiError } from '../../../api/client/errors';
import type { FolderCreate, FolderOut, FolderUpdate } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseFoldersReturn = {
  folders: FolderOut[];
  loading: boolean;
  mutating: boolean;
  error: UiError | null;
  create: (payload: FolderCreate) => Promise<FolderOut>;
  update: (folderId: string, payload: FolderUpdate) => Promise<FolderOut>;
  remove: (folderId: string) => Promise<void>;
  refresh: () => Promise<void>;
};

// One hook for the whole folders CRUD surface (mirrors ``useVirtualMailboxes``):
// no per-verb hooks. The list is keyed ``['folders']`` — the SAME key the
// read-only twins in ``features/emails`` (assign menu) and ``features/mailboxes``
// (sidebar) use, so TanStack dedupes every reader to one fetch. Every mutation
// invalidates ``['folders']`` (contents/counters change) and — because deleting
// a folder cascades to the rules that target it — a delete also invalidates
// ``['rules']`` and the folder listings.
export default function useFolders(): UseFoldersReturn {
  const queryClient = useQueryClient();
  const queryKey = ['folders'] as const;

  const listQuery = useQuery({
    queryKey,
    queryFn: ({ signal }) => listFolders(signal),
  });

  const invalidate = useCallback(() => {
    return queryClient.invalidateQueries({ queryKey });
  }, [queryClient]);

  const createMutation = useMutation({
    mutationFn: (payload: FolderCreate) => createFolder(payload),
    onSuccess: invalidate,
  });

  const updateMutation = useMutation({
    mutationFn: ({ folderId, payload }: { folderId: string; payload: FolderUpdate }) =>
      updateFolder(folderId, payload),
    onSuccess: () => {
      invalidate();
      // A rename/recolor changes the chips rendered in every listing and the
      // single-folder read that feeds the folder view header (['folder', id]).
      queryClient.invalidateQueries({ queryKey: ['folder'] });
      queryClient.invalidateQueries({ queryKey: ['emails'] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
      queryClient.invalidateQueries({ queryKey: ['folder-emails'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (folderId: string) => deleteFolder(folderId),
    onSuccess: () => {
      invalidate();
      // Deleting a folder cascades to the rules that point at it and removes
      // its memberships, so refresh rules and every listing's chips too.
      queryClient.invalidateQueries({ queryKey: ['rules'] });
      queryClient.invalidateQueries({ queryKey: ['emails'] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
      queryClient.invalidateQueries({ queryKey: ['folder-emails'] });
    },
  });

  const create = useCallback(
    async (payload: FolderCreate) => createMutation.mutateAsync(payload),
    [createMutation],
  );
  const update = useCallback(
    async (folderId: string, payload: FolderUpdate) =>
      updateMutation.mutateAsync({ folderId, payload }),
    [updateMutation],
  );
  const remove = useCallback(
    async (folderId: string) => {
      await deleteMutation.mutateAsync(folderId);
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
    folders: listQuery.data ?? [],
    loading: listQuery.isLoading,
    mutating: createMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
    error,
    create,
    update,
    remove,
    refresh,
  };
}
