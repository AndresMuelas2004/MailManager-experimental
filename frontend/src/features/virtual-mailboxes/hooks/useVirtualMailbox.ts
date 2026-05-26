import { useQuery } from '@tanstack/react-query';

import { getVirtualMailbox } from '../../../api/endpoints/virtualMailboxes';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { VirtualMailboxOut } from '../../../api/types/dto';

type UseVirtualMailboxReturn = {
  record: VirtualMailboxOut | null;
  loading: boolean;
  error: UiError | null;
};

export default function useVirtualMailbox(virtualMailboxId: string): UseVirtualMailboxReturn {
  const query = useQuery({
    queryKey: ['virtual-mailbox', virtualMailboxId] as const,
    queryFn: () => getVirtualMailbox(virtualMailboxId),
    enabled: virtualMailboxId.length > 0,
  });

  return {
    record: query.data ?? null,
    loading: query.isLoading,
    error: query.error ? toUiError(query.error) : null,
  };
}
