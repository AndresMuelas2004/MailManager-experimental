import { useQuery } from '@tanstack/react-query';

import { getFolder } from '../../../api/endpoints/folders';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { FolderOut } from '../../../api/types/dto';

type UseFolderReturn = {
  record: FolderOut | null;
  loading: boolean;
  error: UiError | null;
};

// Single-folder read for the folder view header (name + color). Mirrors
// ``useVirtualMailbox``; keyed ``['folder', folderId]`` (distinct from the
// ``['folders']`` list, so it never collides with the twins).
export default function useFolder(folderId: string): UseFolderReturn {
  const query = useQuery({
    queryKey: ['folder', folderId] as const,
    queryFn: () => getFolder(folderId),
    enabled: folderId.length > 0,
  });

  return {
    record: query.data ?? null,
    loading: query.isLoading,
    error: query.error ? toUiError(query.error) : null,
  };
}
