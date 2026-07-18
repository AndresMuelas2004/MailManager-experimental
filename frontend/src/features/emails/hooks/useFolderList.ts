import { useQuery } from '@tanstack/react-query';

import { listFolders } from '../../../api/endpoints/folders';
import type { FolderOut } from '../../../api/types/dto';

type UseFolderListReturn = {
  folders: FolderOut[];
  loading: boolean;
};

/**
 * Read-only folder catalogue for the per-email assign menu. A twin of
 * ``features/mailboxes/hooks/useFolderList`` and of ``useFolders`` in
 * ``features/folders`` — all three share the ``['folders']`` query key so
 * TanStack Query dedupes every reader to ONE fetch. They live apart (not in
 * lib/) because a feature cannot import a hook from another feature (features
 * §6) and a hook that calls an endpoint cannot live in lib/ (lib §3.3); the
 * shared piece is the endpoint + DTO in api/. Same pattern as the two
 * ``useBackfillStatus`` twins.
 */
export default function useFolderList(): UseFolderListReturn {
  const query = useQuery({
    queryKey: ['folders'] as const,
    queryFn: ({ signal }) => listFolders(signal),
  });
  return { folders: query.data ?? [], loading: query.isLoading };
}
